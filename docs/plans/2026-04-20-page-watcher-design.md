# Page Watcher — Design

**Date:** 2026-04-20

## Goal

A config-driven page monitoring tool built on Playwright + Python that supports two check types:

- **`new_content`** — alerts when new items appear in a list (e.g. new blog posts)
- **`change`** — alerts when a section of a page changes (e.g. hero banner, price)

## File Structure

```
flick/
├── watcher.py       # main runner (all shared logic lives here)
├── config.json      # defines what to watch
├── state.json       # auto-generated, persists previous state
└── check_stock.py   # existing script, unchanged
```

## `config.json` Format

```json
{
  "interval_minutes": 30,
  "checkers": [
    {
      "name": "flickaway-blog",
      "url": "https://flickaway.bic.com/blogs/news",
      "type": "new_content",
      "selector": "article h2 a",
      "notify_title": "New Flickaway blog post!"
    },
    {
      "name": "homepage-hero",
      "url": "https://flickaway.bic.com",
      "type": "change",
      "selector": "#shopify-section-hero",
      "notify_title": "Flickaway homepage changed!"
    }
  ]
}
```

- `selector` is a CSS selector scoping what gets monitored. Omitting it falls back to the full page body.
- `interval_minutes` is global, shared across all checkers.

## `state.json` Format

Auto-generated and updated after every check. Keyed by checker `name`:

```json
{
  "flickaway-blog": [
    "New Weekender Colors Drop",
    "Spring 2026 Lookbook"
  ],
  "homepage-hero": "a3f8c2d19e..."
}
```

- **`new_content`** stores a list of seen item identifiers (text + href combined).
- **`change`** stores a SHA-256 hash of the selected element's HTML.
- On first run, no notification is sent — baseline is established silently.

## Core Flow

```
startup
  └── load config.json
  └── load state.json (or empty dict if missing)

loop forever:
  for each checker in config:
    spinner.start()
    fetch page with Playwright → extract selector content

    if type == "new_content":
      compare extracted items vs state[name]
      if new items found → send_notification() → update state

    if type == "change":
      hash extracted HTML
      if hash differs from state[name] → send_notification() → update state

    save state.json
    spinner.stop()

  animated_sleep(interval_minutes)
```

## Shared Utilities (reused from `check_stock.py`)

- `Spinner` — animated terminal spinner
- `animated_sleep` — sleeping pet animation with countdown
- `send_notification` — ntfy.sh push via `NTFY_TOPIC` env var (same SSL workaround)

Checkers run sequentially per interval — no parallelism, no race conditions on `state.json`.

## Environment Variables

| Variable               | Description                          |
|------------------------|--------------------------------------|
| `NTFY_TOPIC`           | ntfy.sh topic for all notifications  |
| `CHECK_INTERVAL_MINUTES` | Override interval (optional)       |
