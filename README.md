# Damavand Bot

Persian Telegram storefront for 3x-ui. It manages optional channel membership and Terms checks,
plans, monthly trials, renewals, discounts, wallets, and referral rewards.
New plan and renewal payments use the wallet. Card-to-card transfers top up the wallet after admin approval. A card receipt is **not** proof of payment: an admin must
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
setup in the panel. If channel membership is required, add the bot as a channel administrator so it
can check membership. For private channels, enter its numeric `-100…` ID and
an `https://t.me/+…` join link; for public channels, a username works, though
the numeric ID is more reliable. The bot verifies the 3x-ui TLS certificate;
use a trusted certificate for its HTTPS endpoint.

After installation, `sudo damavand-bot` opens the menu:

1. Install
2. Update from GitHub (fast-forward only; refuses local checkout changes)
3. Web panel access instructions
4. Uninstall (private data preserved unless separately confirmed)
5. Reset panel password (generates a new one in `/root/damavand-panel-password`)
6. Obtain SSL for a subdomain and expose the panel
7. Expose the panel using an existing HTTPS certificate

The same actions are available as `sudo damavand-bot update`, `panel`,
`uninstall`, and `password`.

## Management panel

The panel initially listens on `127.0.0.1:8765` only. From your computer,
forward it over SSH:

```bash
ssh -L 8765:127.0.0.1:8765 YOUR_USER@YOUR_SERVER
```

Then open <http://127.0.0.1:8765> and sign in. The panel shows users and
orders and edits plans/prices, user- or plan-specific discounts, assigned
subscription links for existing users, channel ID/join link/support, 3x-ui settings,
payment method/card details, trial settings, wallet balances, top-ups, referrals, wallet ledger, and versioned Terms. The Bot buttons
page enables or disables the eight user-facing menu buttons; the admin-only discount
button is no longer shown. Disabling a button also blocks its new entry requests,
while already-started orders can still finish. The sidebar
provides English and Persian navigation. Settings are grouped into Bot,
Payment, Channel, and Trial sections and saved together. Channel membership
is required by default; turn it off in Channel settings to let users proceed
without joining or configuring a channel. Saved Bot and 3x-ui
tokens stay masked until you click Show. The reveal request requires the active
panel session and CSRF token; leave token fields blank to retain saved values.
The Welcome settings section edits the text shown before START, the welcome
message, and the banner title and details shown with the image after `/start`.
It also previews the current image and accepts a replacement PNG or JPEG of up
to 5 MB, with an option to restore the default banner. The image is stored in
the private server configuration directory; it is never committed to Git.
The welcome text and banner fields support `{name}` and `{requirements}` placeholders.
If their combined text exceeds Telegram's photo caption limit, the bot sends
the welcome message separately. Telegram's pre-START description is text-only;
the image is delivered as the first bot message.
Assign an existing service by a user's numeric Telegram ID, even before they
start the bot. You can also enter an `@username` that has not started the bot:
the assignment waits in the panel and attaches automatically to the first
Telegram account with that username to send `/start`. Once attached, it stays
bound to that account's numeric ID and appears in My Services. Because Telegram
usernames can change or be reassigned, use the numeric ID when available.
Imported links are for viewing in My Services; assigning one does not modify
the 3x-ui client or enable bot-managed renewal for it.
Config changes restart the bot; plan and discount changes apply immediately.
Wallet top-ups can be approved in the bot or the panel, but verify the actual bank deposit first. The bot also supports `/pendingtopups` for admins.
Existing card orders and already paid Stars orders continue through their earlier fulfillment paths; new checkout uses the wallet.

## Wallet and referrals

The first `/start` creates a wallet with **5,000 toman** for a new Telegram account.
Existing accounts are migrated with a zero balance. Each user can open **Wallet and referrals**
or `/wallet` to see their balance. The separate **My referral link** button (or
`/referral`) sends a shareable Damavand banner with their personal
`https://t.me/<bot>?start=ref_<telegram_id>` link in its caption and a clickable
link button. The wallet screen also has a button for this banner.
The referral attaches only when a new account first starts the bot through that link;
it cannot be reassigned later. The referrer receives **10% of the discounted toman amount**
after the invited user’s first completed, non-free purchase. Renewals and later purchases
do not generate another reward. A 100% discount does not consume the paid referral reward.

To fund the wallet, a user enters a toman amount, transfers it to the configured card,
and sends a receipt photo. Admin approval credits the wallet once; rejecting it credits
nothing. The user then presses **Pay from wallet** on the order. The balance is checked and
debited atomically. If service creation fails, the paid order remains available for admin
retry without another wallet debit. The wallet ledger in the panel records every credit
and debit. If a failed wallet order cannot be fulfilled, verify in 3x-ui that the
service was neither created nor renewed before using the admin command
`/refundwallet <order_id>`; it refunds that order once. New top-ups require
card-to-card payment to be enabled in Payment settings.

The panel has a password, rate-limited sign-in, CSRF tokens, restrictive
browser headers, a local-only listener, and no root privileges. Treat its
password and your SSH account as administrator credentials. Never expose the
Python backend or send credentials over public HTTP. Rotate any token that was
previously shared or published.

For direct HTTPS, first create a DNS-only A record for a subdomain that points
to your VPS. Do not create a Cloudflare Tunnel or provide a DNS API key. Open
inbound TCP 80 for certificate validation and renewal, and TCP 8765 for the
panel; keep the VPN and x-ui ports unchanged. The installer asks for the
subdomain. If DNS is not ready, press Enter and set it up later with:

```bash
sudo damavand-bot setup-domain bot.example.com
```

Certbot obtains a certificate for that exact name using port 80. The panel
then opens at `https://bot.example.com:8765`. Certbot handles renewals and
the panel's nginx reload timer picks them up. Port 80 must remain reachable
for HTTP-01 renewals. If your DNS provider offers a proxy, use DNS-only mode
because proxying may not support port 8765.

Alternatively, if you already have a certificate covering the domain, use it
directly (the certificate and private key stay root-readable; the bot process
never receives the key):

```bash
sudo damavand-bot expose-domain YOUR_DOMAIN /path/to/fullchain.pem /path/to/privkey.pem
```

The command verifies the certificate and matching key, installs a dedicated
nginx reverse proxy on HTTPS port 8765, and keeps the bot panel bound to
`127.0.0.1:8766`. Nginx reloads daily to pick up certificate renewals; your
existing certificate renewal job must still continue to run. A separate bot
subdomain is preferable to using the exact x-ui hostname because browser
cookies are shared across ports on one hostname. Plain public `http://` is
not supported because it would expose administrator credentials and bot tokens.

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

The panel frontend source is in `web/`. Runtime assets are checked in, so
installing or updating on the VPS does not require Node.js. To rebuild after
changing the UI, use Node.js 20+ and run `npm ci && npm run check && npm run build`
inside `web/`, then commit the generated `damavand_bot/static/` files.

The installer keeps runtime secrets outside the repository.
`damavand-vpn-bot.env.example` is placeholder-only and for manual development,
not production. Never commit live tokens, bank details, databases, or SSH keys.
