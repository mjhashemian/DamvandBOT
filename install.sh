#!/usr/bin/env bash
# Damavand Bot installer / maintenance menu for Debian and Ubuntu.
set -Eeuo pipefail

REPO_URL='https://github.com/mjhashemian/DamvandBOT.git'
APP_DIR='/opt/damavand-vpn-bot'
CONFIG_DIR='/etc/damavand-vpn-bot'
DATA_DIR='/var/lib/damavand-vpn-bot'
CLI_PATH='/usr/local/bin/damavand-bot'
BOT_UNIT='/etc/systemd/system/damavand-vpn-bot.service'
PANEL_UNIT='/etc/systemd/system/damavand-vpn-panel.service'
SERVICE_USER='damavand-bot'

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo 'Run with sudo or as root.' >&2
    exit 1
  fi
  if ! command -v apt-get >/dev/null; then
    echo 'This installer currently supports Debian/Ubuntu (apt) only.' >&2
    exit 1
  fi
}

check_checkout() {
  [[ -d "$APP_DIR/.git" ]] || { echo "No managed checkout at $APP_DIR" >&2; exit 1; }
  local actual
  actual=$(git -C "$APP_DIR" remote get-url origin)
  [[ "$actual" == "$REPO_URL" ]] || { echo 'Checkout remote does not match the official repository; refusing.' >&2; exit 1; }
}

install_bot() {
  require_root
  if [[ -e "$APP_DIR" ]]; then
    check_checkout
    echo 'Existing installation found. Use update instead.'
    return
  fi
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates git python3 python3-venv
  git clone --depth 1 --branch main "$REPO_URL" "$APP_DIR"
  python3 -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
  fi
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$CONFIG_DIR" "$DATA_DIR"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR" "$DATA_DIR"
  if [[ ! -e "$CONFIG_DIR/terms-fa.txt" ]]; then
    install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 "$APP_DIR/config/terms-fa.txt" "$CONFIG_DIR/terms-fa.txt"
  fi
  echo 'Bot setup:'
  cd "$APP_DIR"
  runuser -u "$SERVICE_USER" -- "$APP_DIR/.venv/bin/python" -m damavand_bot.setup
  install -m 0644 "$APP_DIR/damavand-vpn-bot.service" "$BOT_UNIT"
  install -m 0644 "$APP_DIR/damavand-vpn-panel.service" "$PANEL_UNIT"
  install -m 0755 "$APP_DIR/install.sh" "$CLI_PATH"
  systemctl daemon-reload
  systemctl enable --now damavand-vpn-bot.service damavand-vpn-panel.service
  echo 'Installed. The panel listens only on 127.0.0.1:8765.'
  show_panel
}

update_bot() {
  require_root
  check_checkout
  git -C "$APP_DIR" pull --ff-only origin main
  "$APP_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"
  install -m 0644 "$APP_DIR/damavand-vpn-bot.service" "$BOT_UNIT"
  install -m 0644 "$APP_DIR/damavand-vpn-panel.service" "$PANEL_UNIT"
  install -m 0755 "$APP_DIR/install.sh" "$CLI_PATH"
  systemctl daemon-reload
  systemctl restart damavand-vpn-bot.service damavand-vpn-panel.service
  systemctl --no-pager --full status damavand-vpn-bot.service damavand-vpn-panel.service || true
}

show_panel() {
  require_root
  echo 'On your own computer, open an SSH tunnel:'
  echo '  ssh -L 8765:127.0.0.1:8765 YOUR_USER@YOUR_SERVER'
  echo 'Then open http://127.0.0.1:8765 in your browser.'
  echo 'The panel is intentionally not exposed on a public port.'
}

uninstall_bot() {
  require_root
  check_checkout
  local answer
  read -r -p 'Type UNINSTALL to remove the bot services, code and virtual environment: ' answer
  [[ "$answer" == 'UNINSTALL' ]] || { echo 'Cancelled.'; return; }
  systemctl disable --now damavand-vpn-bot.service damavand-vpn-panel.service || true
  rm -f -- "$BOT_UNIT" "$PANEL_UNIT" "$CLI_PATH"
  systemctl daemon-reload
  if [[ "$(realpath -e "$APP_DIR")" != "$APP_DIR" ]]; then
    echo 'Unexpected application path; checkout left in place.' >&2
  else
    rm -rf -- "$APP_DIR"
  fi
  echo "Private configuration and database were preserved at $CONFIG_DIR and $DATA_DIR."
  read -r -p 'Type DELETE-DATA to permanently delete those too (anything else preserves them): ' answer
  if [[ "$answer" == 'DELETE-DATA' ]]; then
    if [[ "$(realpath -e "$CONFIG_DIR")" == "$CONFIG_DIR" && "$(realpath -e "$DATA_DIR")" == "$DATA_DIR" ]]; then
      rm -rf -- "$CONFIG_DIR" "$DATA_DIR"
      echo 'Private configuration and database deleted; no automatic recovery is available.'
    else
      echo 'Unexpected data path; private data preserved.' >&2
    fi
  fi
  if id "$SERVICE_USER" >/dev/null 2>&1; then userdel "$SERVICE_USER" || true; fi
  echo 'Uninstalled. Shared system Python, Git and CA packages were kept for other applications.'
}

password_reset() {
  require_root
  check_checkout
  cd "$APP_DIR"
  runuser -u "$SERVICE_USER" -- "$APP_DIR/.venv/bin/python" -m damavand_bot.setup password
  systemctl restart damavand-vpn-panel.service
}

menu() {
  echo '1) Install'
  echo '2) Update from GitHub'
  echo '3) Web panel access'
  echo '4) Uninstall'
  echo '5) Reset panel password'
  local answer
  read -r -p 'Choose 1-5: ' answer
  case "$answer" in
    1) install_bot ;; 2) update_bot ;; 3) show_panel ;;
    4) uninstall_bot ;; 5) password_reset ;; *) echo 'Invalid choice' >&2; exit 1 ;;
  esac
}

case "${1:-menu}" in
  install) install_bot ;; update) update_bot ;; panel) show_panel ;;
  uninstall) uninstall_bot ;; password) password_reset ;; menu) menu ;;
  *) echo 'Usage: damavand-bot [install|update|panel|uninstall|password]' >&2; exit 1 ;;
esac
