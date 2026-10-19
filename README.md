# Damavand Bot

Persian Telegram storefront for 3x-ui. It manages channel and Terms checks,
plans, monthly trials, renewals, discounts, Telegram Stars, and optional
card-receipt orders. A card receipt is **not** proof of payment: an admin must
verify the deposit independently before approving it in Telegram.

## One-command installation (Ubuntu/Debian)

Review [`install.sh`](install.sh) before running remote code as root. It clones
this repository, creates an unprivileged service account and virtual
environment, and generates an initial panel password. Retrieve it on the VPS
with `sudo cat /root/damavand-panel-password`, then change it with
`sudo damavand-bot password` (which removes the bootstrap password file).
After signing in,
enter the bot token, Telegram admin IDs, channel/support details, 3x-ui
URL/API token, and subscription URL in the panel. It does not install or change 3x-ui.

```bash
curl -fsSL https://raw.githubusercontent.com/mjhashemian/DamvandBOT/main/install.sh | sudo bash -s -- install
```

A BotFather token, a 3x-ui Clients API token, and a reachable 3x-ui API are needed when you finish
setup in the panel. Add the bot as a channel administrator so it
can check membership. For private channels, enter its numeric `-100…` ID and
an `https://t.me/+…` join link; for public channels, a username works, though
the numeric ID is more reliable. Verify the 3x-ui TLS certificate before disabling TLS
verification for a local self-signed endpoint.

After installation, `sudo damavand-bot` opens the menu:

1. Install
2. Update from GitHub (fast-forward only; refuses local checkout changes)
3. Web panel access instructions
4. Uninstall (private data preserved unless separately confirmed)
5. Reset panel password
6. Expose the panel using an existing HTTPS certificate

The same actions are available as `sudo damavand-bot update`, `panel`,
`uninstall`, and `password`.

## Management panel

The panel initially listens on `127.0.0.1:8765` only. From your computer,
forward it over SSH:

```bash
ssh -L 8765:127.0.0.1:8765 YOUR_USER@YOUR_SERVER
```

Then open <http://127.0.0.1:8765> and sign in. The panel shows users and
orders and edits plans/prices, discounts, channel ID/join link/support, 3x-ui settings,
payment method/card details, trial settings, and versioned Terms. Config
changes restart the bot; plan and discount changes apply immediately. Receipt
approval and failed-order retries remain in Telegram, where the existing
provisioning flow handles them.

The panel has a password, rate-limited sign-in, CSRF tokens, restrictive
browser headers, a local-only listener, and no root privileges. Treat its
password and your SSH account as administrator credentials. Never expose the
Python backend or send credentials over public HTTP. Rotate any token that was
previously shared or published.

To expose it through a domain whose existing certificate already covers that
name, run this on the VPS (the certificate and private key stay root-readable;
the bot process never receives the key):

```bash
sudo damavand-bot expose-domain YOUR_DOMAIN /path/to/fullchain.pem /path/to/privkey.pem
```

The command verifies the certificate and matching key, installs a dedicated
nginx reverse proxy on HTTPS port 8765, and keeps the bot panel bound to
`127.0.0.1:8766`. Open only TCP 8765 in your cloud firewall. Nginx reloads
daily to pick up certificate renewals; your existing certificate renewal job
must still continue to run. A separate bot subdomain is preferable to using
the exact x-ui hostname because browser cookies are shared across ports on
one hostname. Plain `http://` is not supported for public access.

## Files and lifecycle

| Path | Purpose |
| --- | --- |
| `/opt/damavand-vpn-bot` | Git checkout and Python virtual environment |
| `/etc/damavand-vpn-bot/config.json` | Private settings/tokens, mode `0600` |
| `/etc/damavand-vpn-bot/panel-auth.json` | Salted password hash, mode `0600` |
| `/etc/damavand-vpn-bot/terms-fa.txt` | Editable Terms |
| `/var/lib/damavand-vpn-bot/bot.db` | SQLite orders, users, plans |
| `damavand-vpn-bot.service` | Telegram bot |
| `damavand-vpn-panel.service` | Local web panel |

Back up `/etc/damavand-vpn-bot` and `/var/lib/damavand-vpn-bot` securely
before updating or uninstalling. Uninstall removes only this app's checkout,
virtual environment, systemd units, and service account. Private data is
preserved by default and deleted only after separate confirmation. Shared
system packages and 3x-ui are left alone.

Diagnostics:

```bash
sudo systemctl status damavand-vpn-bot damavand-vpn-panel
sudo journalctl -u damavand-vpn-bot -u damavand-vpn-panel -n 100 --no-pager
```

## Development

Python 3.11+:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

The installer keeps runtime secrets outside the repository.
`damavand-vpn-bot.env.example` is placeholder-only and for manual development,
not production. Never commit live tokens, bank details, databases, or SSH keys.
