# ⚡ Crypton Intel – Quantitative Market Intelligence & Signal Pipeline

An asynchronous quantitative market intelligence pipeline engineered in Python that streams real-time cryptocurrency tick data, calculates multi-indicator trend and momentum confluences, and delivers automated market structure digests directly via Telegram.

🔗 **Live Telegram Bot Interface:** [@Alert71_bot](https://t.me/Alert71_bot)

---

## 📌 System Architecture & Objectives

Cryptocurrency derivatives and spot markets operate continuously with high volatility, fragmented liquidity, and high noise-to-signal ratios. Crypton Intel provides automated, algorithmic signal filtration to evaluate market regimes without manual screen monitoring.

* **Asynchronous Market Ingestion:** Stream continuous OHLCV candlestick data and derivatives liquidation events using asynchronous WebSocket and REST client pipelines.
* **Deterministic Confluence Modeling:** Eliminate subjective chart interpretation by evaluating mathematical momentum, trend deviation, and structural volatility thresholds.
* **Automated Broadcast Delivery:** Dispatch formatted 4-hour market structure summaries, sudden volume alerts, and momentum regime shifts to a dedicated Telegram interface.
* **Cloud Resilience:** Configured for continuous 24/7 background worker execution with automatic reconnection and rate-limit backoff handling.

---

## 🛠️ Technical Stack & Frameworks

* **Language:** Python 3.11+
* **Async Engine & Networking:** `asyncio`, `aiohttp`, `websockets`
* **Data Manipulation & Math:** NumPy, Pandas
* **Exchange Connectivity:** Binance Spot & USD-M Futures Public APIs
* **Messaging & Delivery:** Python Telegram Bot API (Async Webhook / Polling)
* **Infrastructure & Deployment:** Railway / Linux Cloud Worker Environment

---

## ⚙️ Quantitative Signal & Confluence Engine

The core signal engine processes price series through a deterministic 7-factor confluence model across multiple timeframes (15m, 1h, 4h):

### 1. Momentum & Regime Detection
* **14-Period Wilder's Smoothed RSI:** Evaluates dynamic overbought (>70) and oversold (<30) momentum bounds, tracking bullish and bearish divergence patterns against price peaks.
* **Multi-Tier Trend Ribbon:** Computes Exponential Moving Averages across short, medium, and long horizons (21 EMA, 50 EMA, 200 EMA) to categorize market state into Expansion, Compression, or Trend Reversal regimes.

### 2. Market Structure & Volatility Mechanics
* **Break of Structure (BOS) / Change of Character (CHoCH):** Algorithmic detection of higher-high / lower-low structural violations across rolling swing highs and swing lows.
* **Volume Delta & Spike Thresholding:** Flags volume candles exceeding $2.5\times$ the rolling 20-period volume moving average.
* **Liquidation Cascade Monitoring:** Monitors anomalous clusters of long/short futures liquidations to identify potential local exhaustion or squeeze zones.

---

## 📊 Automated Dispatch Workflow

```text
[Binance WebSocket / REST API] 
              │
              ▼
   [Async Ingestion Engine]
              │
              ▼
  [Rolling Memory Buffer / DF]
              │
              ▼
   [7-Factor Confluence Model] ── (Condition Trigger Met)
              │                               │
              ▼                               ▼
     [Scheduled 4H Digest]          [Urgent Volatility Alert]
              │                               │
              └───────────────┬───────────────┘
                              │
                              ▼
                     [Telegram Bot API]
                              │
                              ▼
                 [@Alert71_bot Channel Stream]
