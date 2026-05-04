import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from utils.app_state import get_db, get_bot_app

import asyncio
import hashlib
import hmac
import json
import os

from fastapi import FastAPI, Header, HTTPException, Request
import uvicorn

import database

app = FastAPI()
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")
PRO_PRICE_INR = float(os.getenv("PRO_PRICE_INR", "249"))
PRO_PRICE_PAISE = int(round(PRO_PRICE_INR * 100))
PRO_CURRENCY = os.getenv("PRO_CURRENCY", "INR").upper()

# This will be set by main.py to allow sending messages from the webhook
bot_session = None
TELEGRAM_API_URL = None


def _extract_entity(payload):
    subscription_entity = payload.get("subscription", {}).get("entity")
    payment_entity = payload.get("payment", {}).get("entity")
    return subscription_entity or payment_entity


def _validate_entity(event, entity):
    user_id = str(entity.get("notes", {}).get("user_id", "")).strip()
    payment_id = str(entity.get("id", "")).strip()
    amount_paise = int(entity.get("amount", 0) or 0)
    amount = amount_paise / 100
    currency = str(entity.get("currency", "")).upper()
    status = str(entity.get("status", "")).lower()
    plan_id = entity.get("plan_id", "pro_monthly")

    if not payment_id:
        return None, {"status": "ok", "action": "skipped_no_payment_id"}, (
            f"Webhook payload missing payment id for event={event}. Skipping processing."
        )
    if not user_id:
        return None, {"status": "ok", "action": "missing_user_id"}, (
            f"CRITICAL: Payment {payment_id} received for event={event} but no user_id found in notes. "
            "Manual intervention required."
        )
    if currency != PRO_CURRENCY:
        return None, {"status": "ok", "action": "invalid_currency"}, (
            f"Rejected payment {payment_id} for user {user_id}: currency {currency!r} != {PRO_CURRENCY!r}."
        )
    if amount_paise != PRO_PRICE_PAISE:
        return None, {"status": "ok", "action": "invalid_amount"}, (
            f"Rejected payment {payment_id} for user {user_id}: amount {amount_paise} != {PRO_PRICE_PAISE} paise."
        )
    if event == "payment.captured" and status != "captured":
        return None, {"status": "ok", "action": "invalid_status"}, (
            f"Rejected payment {payment_id} for user {user_id}: expected captured status, got {status!r}."
        )
    if event == "subscription.activated" and status != "active":
        return None, {"status": "ok", "action": "invalid_status"}, (
            f"Rejected subscription {payment_id} for user {user_id}: expected active status, got {status!r}."
        )

    return {
        "user_id": user_id,
        "payment_id": payment_id,
        "amount": amount,
        "currency": currency,
        "plan_id": plan_id,
    }, None, None


@app.post("/razorpay-webhook")
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(None)):
    body = await request.body()

    if not RAZORPAY_WEBHOOK_SECRET:
        print("RAZORPAY_WEBHOOK_SECRET not set. Webhook rejected.")
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    expected_signature = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not x_razorpay_signature or not hmac.compare_digest(expected_signature, x_razorpay_signature):
        print("Razorpay invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = json.loads(body)
    event = data.get("event")
    print(f"Razorpay webhook received: {event}")

    if event not in {"payment.captured", "subscription.activated"}:
        print(f"Ignoring unhandled event type: {event}")
        return {"status": "ignored", "reason": "unhandled_event"}

    entity = _extract_entity(data.get("payload", {}))
    if not entity:
        print(f"Webhook payload missing entity for event={event}.")
        return {"status": "ok", "action": "missing_entity"}

    validated, early_response, log_message = _validate_entity(event, entity)
    if early_response:
        print(log_message)
        return early_response

    user_id = validated["user_id"]
    payment_id = validated["payment_id"]
    amount = validated["amount"]
    currency = validated["currency"]
    plan_id = validated["plan_id"]

    inserted = await asyncio.to_thread(database.record_payment, payment_id, user_id, event, amount, currency)
    if not inserted:
        print(f"Payment {payment_id} already processed. Skipping duplicate webhook.")
        return {"status": "ok", "action": "duplicate_skipped"}

    await asyncio.to_thread(database.upgrade_user, user_id, plan_id, amount, currency)
    print(f"User {user_id} upgraded to PRO via Razorpay ({event}). Payment: {payment_id}")

    if bot_session and TELEGRAM_API_URL:
        msg = (
            "PAYMENT CONFIRMED\n\n"
            "Congratulations! Your account has been upgraded to Pro Tier.\n"
            "You now have access to personalized intelligence, Gemini-synthesized news, and exclusive market digests.\n\n"
            "Use /mycoins to set your watchlist if you haven't already!"
        )
        url = f"{TELEGRAM_API_URL}/sendMessage"
        tg_payload = {"chat_id": user_id, "text": msg, "parse_mode": "HTML"}
        try:
            async with bot_session.post(url, json=tg_payload) as resp:
                response_body = await resp.text()
                if resp.status != 200:
                    print(
                        f"Telegram confirmation returned {resp.status} for user {user_id}: "
                        f"{response_body[:200]}"
                    )
        except Exception as exc:
            print(f"Failed to send upgrade confirmation to {user_id}: {exc}")

    return {"status": "ok"}


async def run_webhook_server(session, tg_url):
    global bot_session, TELEGRAM_API_URL
    bot_session = session
    TELEGRAM_API_URL = tg_url

    port = int(os.getenv("PORT", 8080))
    print(f"Razorpay Webhook Server starting on port {port}...")
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
