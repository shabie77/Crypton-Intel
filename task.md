# Beginner's Guide & Task List: Free Crypto Intelligence Bot

This checklist is perfectly tailored for a complete beginner on a strict budget. We will use **100% free tools and APIs**, and we will build and run this bot directly from your personal computer—meaning absolutely **$0 in hosting or database costs** to start.

To keep things extremely simple, we are swapping out complex enterprise databases (like Redis/PostgreSQL) for **SQLite**, which is completely free and built directly into Python (no installation required!).

## Required API Keys (Your First Step)
Before we can write the code to bring the bot to life, I need you to generate two entirely free API keys. Once you have them, paste them here to me, and we will begin Phase 1.

1. **Telegram Bot Token (For sending alerts):**
   - Open your Telegram app and search for the user `@BotFather` (ensure it has the blue verified checkmark).
   - Send the message `/newbot`.
   - Follow the prompts to give your bot a Name and a Username.
   - BotFather will reply with a long string of letters and numbers called an **HTTP API Token**. Copy and save this.
   - *Cost: Free*

2. **CryptoPanic API Key (For crypto news):**
   - Go to [CryptoPanic.com](https://cryptopanic.com/).
   - Create a free account.
   - Go to your Account Settings -> API section to generate your free API Token. Copy and save this.
   - *Cost: Free*

*(Note: For our heavy lifting—like tracking real-time liquidations, volume, and determining the market trend—we will connect to the **Binance Public WebSockets and APIs**. These are incredibly fast, public, and do not require any API keys or accounts!)*

---

## Step-by-Step Build Process

### Phase 1: Easy Environment Setup
- `[ ]` Create a new project folder on your computer.
- `[ ]` Set up a Python Virtual Environment (`venv`) to keep our workspace organized.
- `[ ]` Install the necessary free libraries: `aiogram` (for Telegram), `pandas` (for math/scoring), and `websockets` (for Binance data).

### Phase 2: The Telegram Connection
- `[ ]` Write the base script to connect your Python code to Telegram using your Bot Token.
- `[ ]` Test the connection by having the bot automatically send a "System Online" message to your phone.

### Phase 3: Fetching Free Market Data
- `[ ]` **Liquidations:** Connect to Binance's free public WebSocket to listen for massive forced liquidations in real-time.
- `[ ]` **Context (OHLCV):** Fetch live 1-hour and 4-hour Bitcoin price candles using Binance free public endpoints.
- `[ ]` **News:** Connect to the CryptoPanic API to pull breaking crypto headlines.

### Phase 4: Building the "Brain" (Intelligence Layer)
- `[ ]` Write the basic math functions to calculate Simple Moving Averages (SMA) so the bot knows if the trend is Bullish or Bearish.
- `[ ]` Build the **Confluence Scoring Matrix** (e.g., assigning a base score of +5 for big short liquidations).
- `[ ]` Program the dynamic weight logic (e.g., if the trend is bearish, multiply that +5 by 0.5 so we don't get tricked).

### Phase 5: Assembly & Local Database
- `[ ]` Tie the data streams and the "Brain" together so they talk to each other.
- `[ ]` Format the bot's final output output into a beautiful Telegram message.
- `[ ]` Set up our free **SQLite Database** to automatically log every signal the bot sends, so we can track its success rate over time.
- `[ ]` Turn the bot on and watch the alerts roll in!
