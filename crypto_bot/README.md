# Crypto Bot Deployment

This bot can be deployed to an Oracle Cloud Ubuntu VM and updated from GitHub.

## Local project layout

- `main.py`: bot entrypoint
- `database.py`: SQLite helpers
- `.env`: runtime secrets
- `subscribers.db`: SQLite database file

## Required environment variables

Create a `.env` file based on `.env.example`.

- `TELEGRAM_BOT_TOKEN`: your Telegram bot token
- `DB_FILE`: absolute path to the SQLite database on the server

## Oracle Cloud deployment

Use the files in the repository root:

- `scripts/oracle_setup.sh`: one-time server bootstrap
- `scripts/deploy.sh`: pull latest code, install dependencies, restart service
- `deploy/crypto-bot.service`: `systemd` service definition
- `.github/workflows/deploy.yml`: GitHub Actions deployment workflow

See the root `DEPLOYMENT.md` for the full setup flow.
