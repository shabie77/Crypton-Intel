#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${1:-}"
BRANCH="${BRANCH:-main}"
APP_BASE="${APP_BASE:-/opt/crypto_bot}"
APP_ROOT="${APP_BASE}/app"
SHARED_DIR="${APP_BASE}/shared"
SERVICE_NAME="${SERVICE_NAME:-crypto-bot}"
SERVICE_USER="${SERVICE_USER:-ubuntu}"

if [[ -z "${REPO_URL}" ]]; then
  echo "Usage: bash scripts/oracle_setup.sh <github-repo-url>"
  exit 1
fi

sudo apt update
sudo apt install -y git python3 python3-venv python3-pip python3-dev

sudo mkdir -p "${APP_BASE}"
sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_BASE}"

mkdir -p "${SHARED_DIR}"

if [[ ! -d "${APP_ROOT}/.git" ]]; then
  git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_ROOT}"
else
  cd "${APP_ROOT}"
  git fetch origin "${BRANCH}"
  git pull --ff-only origin "${BRANCH}"
fi

if [[ ! -f "${SHARED_DIR}/.env" ]]; then
  cp "${APP_ROOT}/crypto_bot/.env.example" "${SHARED_DIR}/.env"
  echo "Created ${SHARED_DIR}/.env from template. Edit it before starting the service."
fi

mkdir -p "${SHARED_DIR}"
touch "${SHARED_DIR}/subscribers.db"
sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_BASE}"

sudo cp "${APP_ROOT}/deploy/crypto-bot.service" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo sed -i "s|User=ubuntu|User=${SERVICE_USER}|g" "/etc/systemd/system/${SERVICE_NAME}.service"

bash "${APP_ROOT}/scripts/deploy.sh"

sudo systemctl enable "${SERVICE_NAME}"

echo "Setup complete."
echo "Edit ${SHARED_DIR}/.env if needed, then use: sudo systemctl restart ${SERVICE_NAME}"
