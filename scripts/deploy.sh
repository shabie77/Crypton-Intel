#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/crypto_bot/app}"
SHARED_DIR="${SHARED_DIR:-/opt/crypto_bot/shared}"
SERVICE_NAME="${SERVICE_NAME:-crypto-bot}"
BRANCH="${BRANCH:-main}"

echo "Deploying branch ${BRANCH} into ${APP_ROOT}"

mkdir -p "${SHARED_DIR}"
cd "${APP_ROOT}"

git fetch origin "${BRANCH}"
git checkout "${BRANCH}"
git pull --ff-only origin "${BRANCH}"

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r crypto_bot/requirements.txt

if [[ ! -f "${SHARED_DIR}/.env" ]]; then
  echo "Missing ${SHARED_DIR}/.env"
  exit 1
fi

set -a
source "${SHARED_DIR}/.env"
set +a

export RAILWAY_VOLUME_MOUNT_PATH="${RAILWAY_VOLUME_MOUNT_PATH:-${SHARED_DIR}}"
export DB_FILE="${DB_FILE:-${RAILWAY_VOLUME_MOUNT_PATH}/subscribers.db}"

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || "${TELEGRAM_BOT_TOKEN}" == "replace_with_your_bot_token" ]]; then
  echo "Set a real TELEGRAM_BOT_TOKEN in ${SHARED_DIR}/.env before deploying."
  exit 1
fi

if [[ -z "${ADMIN_USER_ID:-}" || "${ADMIN_USER_ID}" == "replace_with_your_telegram_user_id" ]]; then
  echo "Set a real ADMIN_USER_ID in ${SHARED_DIR}/.env before deploying."
  exit 1
fi

mkdir -p "${RAILWAY_VOLUME_MOUNT_PATH}"

if [[ ! -L "${APP_ROOT}/crypto_bot/.env" ]]; then
  rm -f "${APP_ROOT}/crypto_bot/.env"
  ln -s "${SHARED_DIR}/.env" "${APP_ROOT}/crypto_bot/.env"
fi

touch "${DB_FILE}"

sudo systemctl daemon-reload
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl status "${SERVICE_NAME}" --no-pager
