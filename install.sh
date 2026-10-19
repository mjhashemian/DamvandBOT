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
BOOTSTRAP_PASSWORD_FILE='/root/damavand-panel-password'
PANEL_DOMAIN_FILE="$CONFIG_DIR/panel-domain"
NGINX_SITE='/etc/nginx/sites-available/damavand-vpn-panel'
NGINX_LINK='/etc/nginx/sites-enabled/damavand-vpn-panel'
TLS_RELOAD_UNIT='/etc/systemd/system/damavand-vpn-panel-tls-reload.service'
TLS_RELOAD_TIMER='/etc/systemd/system/damavand-vpn-panel-tls-reload.timer'

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
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates git python3 python3-venv
  if [[ -e "$APP_DIR" ]]; then
    check_checkout
  else
    git clone --depth 1 --branch main "$REPO_URL" "$APP_DIR"
  fi
  if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$APP_DIR/.venv"
  fi
  "$APP_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
  fi
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$CONFIG_DIR" "$DATA_DIR"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR" "$DATA_DIR"
  if [[ ! -e "$CONFIG_DIR/terms-fa.txt" ]]; then
    install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 "$APP_DIR/config/terms-fa.txt" "$CONFIG_DIR/terms-fa.txt"
  fi
  cd "$APP_DIR"
  if [[ ! -f "$CONFIG_DIR/panel-auth.json" ]]; then
    local bootstrap_password
    bootstrap_password=$(runuser -u "$SERVICE_USER" -- "$APP_DIR/.venv/bin/python" -m damavand_bot.setup generate)
    (umask 077; printf '%s\n' "$bootstrap_password" > "$BOOTSTRAP_PASSWORD_FILE")
    unset bootstrap_password
    echo "A one-time panel password was generated. Retrieve it with: sudo cat $BOOTSTRAP_PASSWORD_FILE"
  fi
  install -m 0644 "$APP_DIR/damavand-vpn-bot.service" "$BOT_UNIT"
  install -m 0644 "$APP_DIR/damavand-vpn-panel.service" "$PANEL_UNIT"
  install -m 0755 "$APP_DIR/install.sh" "$CLI_PATH"
  systemctl daemon-reload
  systemctl enable --now damavand-vpn-bot.service damavand-vpn-panel.service
  echo 'Installed. Complete bot setup in the web panel.'
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
  if [[ -f "$BOOTSTRAP_PASSWORD_FILE" ]]; then
    echo "Initial panel password: sudo cat $BOOTSTRAP_PASSWORD_FILE"
    echo 'After changing the password, the bootstrap file is removed.'
  fi
  if [[ -f "$PANEL_DOMAIN_FILE" ]]; then
    echo "Open https://$(<"$PANEL_DOMAIN_FILE"):8765"
    echo 'Allow TCP port 8765 in your cloud security group. Plain HTTP is disabled.'
  else
    echo 'On your own computer, open an SSH tunnel:'
    echo '  ssh -L 8765:127.0.0.1:8765 YOUR_USER@YOUR_SERVER'
    echo 'Then open http://127.0.0.1:8765 in your browser.'
    echo 'The panel is intentionally not exposed on a public port.'
  fi
}

expose_domain() {
  require_root
  check_checkout
  local domain="${1,,}" cert="${2:-}" key="${3:-}" cert_pub key_pub
  if [[ ! "$domain" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ || "$domain" != *.* ]]; then
    echo 'Enter a valid public DNS hostname.' >&2; exit 1
  fi
  if [[ ! "$cert" =~ ^/[a-zA-Z0-9._/-]+$ || ! "$key" =~ ^/[a-zA-Z0-9._/-]+$ ]]; then
    echo 'Certificate and key must be absolute paths without spaces.' >&2; exit 1
  fi
  [[ -f "$cert" && -f "$key" ]] || { echo 'Certificate or key not found.' >&2; exit 1; }
  openssl x509 -in "$cert" -noout -checkhost "$domain" | grep -q 'does match' || {
    echo 'Certificate does not cover the chosen domain.' >&2; exit 1;
  }
  cert_pub=$(openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum)
  key_pub=$(openssl pkey -in "$key" -pubout -outform DER | sha256sum)
  [[ "$cert_pub" == "$key_pub" ]] || { echo 'Certificate and key do not match.' >&2; exit 1; }
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nginx
  if [[ -e "$NGINX_SITE" ]] && ! grep -q '^# Managed by Damavand Bot$' "$NGINX_SITE"; then
    echo 'An unmanaged nginx site already uses this name; refusing to overwrite.' >&2; exit 1
  fi
  cat > "$NGINX_SITE" <<EOF
# Managed by Damavand Bot
server {
    listen 8765 ssl;
    server_name $domain;
    ssl_certificate $cert;
    ssl_certificate_key $key;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 64k;
    add_header Strict-Transport-Security "max-age=31536000" always;
    if (\$host != $domain) { return 421; }
    location / {
        proxy_pass http://127.0.0.1:8766;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
}
EOF
  if [[ -L /etc/nginx/sites-enabled/default && "$(readlink /etc/nginx/sites-enabled/default)" == '/etc/nginx/sites-available/default' ]]; then
    unlink /etc/nginx/sites-enabled/default
  fi
  ln -sfn "$NGINX_SITE" "$NGINX_LINK"
  nginx -t
  printf '%s\n' "$domain" > "$PANEL_DOMAIN_FILE"
  chown "$SERVICE_USER:$SERVICE_USER" "$PANEL_DOMAIN_FILE"
  chmod 0600 "$PANEL_DOMAIN_FILE"
  cat > "$TLS_RELOAD_UNIT" <<'EOF'
[Unit]
Description=Reload nginx to pick up renewed Damavand panel certificate

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl reload nginx.service
EOF
  cat > "$TLS_RELOAD_TIMER" <<'EOF'
[Unit]
Description=Daily nginx certificate refresh for Damavand panel

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now damavand-vpn-panel-tls-reload.timer
  systemctl restart damavand-vpn-panel.service
  systemctl enable --now nginx.service
  systemctl reload nginx.service
  show_panel
}

uninstall_bot() {
  require_root
  check_checkout
  local answer
  read -r -p 'Type UNINSTALL to remove the bot services, code and virtual environment: ' answer
  [[ "$answer" == 'UNINSTALL' ]] || { echo 'Cancelled.'; return; }
  systemctl disable --now damavand-vpn-bot.service damavand-vpn-panel.service || true
  systemctl disable --now damavand-vpn-panel-tls-reload.timer 2>/dev/null || true
  if [[ -L "$NGINX_LINK" && "$(readlink "$NGINX_LINK")" == "$NGINX_SITE" ]]; then unlink "$NGINX_LINK"; fi
  if [[ -f "$NGINX_SITE" ]] && grep -q '^# Managed by Damavand Bot$' "$NGINX_SITE"; then rm -f -- "$NGINX_SITE"; fi
  if command -v nginx >/dev/null; then nginx -t && systemctl reload nginx.service || true; fi
  rm -f -- "$PANEL_DOMAIN_FILE" "$TLS_RELOAD_UNIT" "$TLS_RELOAD_TIMER" "$BOOTSTRAP_PASSWORD_FILE"
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
  rm -f -- "$BOOTSTRAP_PASSWORD_FILE"
  systemctl restart damavand-vpn-panel.service
}

menu() {
  echo '1) Install'
  echo '2) Update from GitHub'
  echo '3) Web panel access'
  echo '4) Uninstall'
  echo '5) Reset panel password'
  echo '6) Expose panel on an existing TLS domain'
  local answer
  read -r -p 'Choose 1-6: ' answer
  case "$answer" in
    1) install_bot ;; 2) update_bot ;; 3) show_panel ;;
    4) uninstall_bot ;; 5) password_reset ;;
    6) echo 'Run: sudo damavand-bot expose-domain DOMAIN FULLCHAIN_PATH PRIVATE_KEY_PATH' ;;
    *) echo 'Invalid choice' >&2; exit 1 ;;
  esac
}

case "${1:-menu}" in
  install) install_bot ;; update) update_bot ;; panel) show_panel ;;
  expose-domain) expose_domain "${2:-}" "${3:-}" "${4:-}" ;;
  uninstall) uninstall_bot ;; password) password_reset ;; menu) menu ;;
  *) echo 'Usage: damavand-bot [install|update|panel|uninstall|password|expose-domain DOMAIN CERT KEY]' >&2; exit 1 ;;
esac
