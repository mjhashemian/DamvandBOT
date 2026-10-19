# Damavand VPN Telegram bot

Persian Telegram storefront connected to the first-class Clients API in 3x-ui.

The bot uses Telegram Stars for paid digital services. Card-to-card payments are
intentionally not implemented because Telegram requires Stars for digital goods
and services sold inside bots.

## Features

- Mandatory channel membership and versioned Terms acceptance
- Persian menu, support and channel links
- Stars invoices with idempotent fulfilment
- 500 MiB / one-day monthly test service
- New clients attached to every enabled VLESS inbound
- Renewal of existing clients
- Subscription QR and individual inbound links
- SQLite audit/order database and admin health commands

## Environment

See `damavand-vpn-bot.env.example`. Keep the real file outside the project and
readable only by the service account.

## Admin commands

- `/status`
- `/plans`
- `/setstars <traffic_gb> <stars>`
- `/retryorder <order_id>`

Paid plans remain unavailable while their Stars price is zero. Users also cannot
enter the store until a non-placeholder Terms text is configured.

