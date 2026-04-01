# Deploy This Telegram Bot To Oracle Cloud With GitHub

This project is now prepared for a simple flow:

1. Push the repo to GitHub
2. Clone it once on your Oracle Cloud Ubuntu VM
3. Run the setup script
4. Let GitHub Actions deploy future pushes automatically

## 1. What to keep out of GitHub

Do not upload these:

- `crypto_bot/.env`
- `crypto_bot/subscribers.db`
- `crypto_bot/venv`

They are already covered in `.gitignore`.

## 2. Push this project to GitHub

On your own machine, after Git is installed:

```bash
git init
git add .
git commit -m "Initial crypto bot deployment setup"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

## 3. Create the Oracle Cloud VM

Use an Ubuntu VM on Oracle Cloud.

Open inbound rules for:

- `22` for SSH

You do not need to open a public bot port because this bot uses long polling and outbound API calls.

## 4. Connect to the Oracle VM

From your local machine:

```bash
ssh ubuntu@YOUR_ORACLE_PUBLIC_IP
```

## 5. Run the one-time setup on the server

On the Oracle VM:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git /opt/crypto_bot/app
cd /opt/crypto_bot/app
bash scripts/oracle_setup.sh https://github.com/YOUR_USERNAME/YOUR_REPO.git
```

The setup script will:

- install Python and Git tools
- create `/opt/crypto_bot/shared`
- create `/opt/crypto_bot/shared/.env`
- create `/opt/crypto_bot/shared/subscribers.db`
- install the `systemd` service
- create the virtualenv
- install dependencies
- start the bot

## 6. Add the bot token on the server

Open the server env file:

```bash
nano /opt/crypto_bot/shared/.env
```

Use:

```env
TELEGRAM_BOT_TOKEN=your_real_bot_token
DB_FILE=/opt/crypto_bot/shared/subscribers.db
```

Then restart:

```bash
sudo systemctl restart crypto-bot
sudo systemctl status crypto-bot --no-pager
```

## 7. Check logs

```bash
sudo journalctl -u crypto-bot -f
```

## 8. Enable GitHub auto-deploy

In your GitHub repository, add these Actions secrets:

- `ORACLE_HOST`: your server public IP
- `ORACLE_USER`: usually `ubuntu`
- `ORACLE_SSH_KEY`: the private SSH key GitHub Actions should use
- `ORACLE_PORT`: usually `22`

After that, every push to `main` will run:

```bash
bash /opt/crypto_bot/app/scripts/deploy.sh
```

on the server through GitHub Actions.

## 9. Useful server commands

Restart manually:

```bash
sudo systemctl restart crypto-bot
```

Stop:

```bash
sudo systemctl stop crypto-bot
```

Start:

```bash
sudo systemctl start crypto-bot
```

Logs:

```bash
sudo journalctl -u crypto-bot -n 100 --no-pager
```

## Notes specific to this bot

- The SQLite database path is now configurable with `DB_FILE`.
- `aiohttp` was added to `requirements.txt` because the bot imports it.
- The bot can run fine on Linux; the Windows event loop block only applies on Windows.
