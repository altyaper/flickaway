# Flickaway Weekender Stock Checker

Polls the [Flickaway Weekender](https://flickaway.bic.com/products/420-flickaway-weekender) product page and sends a push notification via [ntfy.sh](https://ntfy.sh) when any variant comes back in stock.

## Requirements

- Python 3.10+
- [Playwright](https://playwright.dev/python/)

## Setup

**1. Install dependencies**

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install playwright
playwright install chromium
```

**2. Configure environment variables**

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `NTFY_TOPIC` | _(empty)_ | Your [ntfy.sh](https://ntfy.sh) topic name. If unset, notifications are skipped. |
| `CHECK_INTERVAL_MINUTES` | `30` | How often (in minutes) to poll the product page. |

> **Tip:** Choose a unique, hard-to-guess topic name on ntfy.sh — it acts as your "password".

**3. Subscribe to notifications (optional)**

Install the [ntfy app](https://ntfy.sh/#subscribe) on your phone or desktop and subscribe to your chosen topic to receive push alerts.

## Running

```bash
source .venv/bin/activate   # if not already active
python check_stock.py
```

The watcher will log each check to the terminal and sleep between polls. Press `Ctrl+C` to stop.

## Example output

```
(^・ω・^) flickaway watcher is awake!
  product  : https://flickaway.bic.com/products/420-flickaway-weekender
  interval : every 30 min
  ntfy     : my-secret-topic

[2026-04-20 10:00:00] (╥_╥)   still sold out... *sniffle*
[2026-04-20 10:30:00] (★^O^★) OMG ITS HERE!! ✨  Black / M
```
