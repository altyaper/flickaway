#!/usr/bin/env python3
"""
Flickaway Weekender stock checker.
Polls the product page and sends a push notification via ntfy.sh when any variant is available.
"""

import json
import os
import re
import ssl
import time
import urllib.request

# Bloomberg's proxy does SSL inspection — use an unverified context for outbound HTTPS
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE
_opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_ctx))

from playwright.sync_api import sync_playwright

PRODUCT_URL = "https://flickaway.bic.com/products/420-flickaway-weekender"
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))

# ntfy.sh config — set NTFY_TOPIC to your chosen topic name (keep it unique/private)
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")


def check_stock() -> tuple[bool, list[str]]:
    """
    Returns (any_available, list_of_available_variant_titles).
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30_000)

        # Shopify stores variant data in a <script> tag as window.Shopify or
        # a plain JSON blob assigned to a variable.
        content = page.content()
        browser.close()

    available = []

    # 1) Try JSON-LD schema (fastest — always present on Shopify)
    ld_matches = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        content,
        re.DOTALL,
    )
    for blob in ld_matches:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        # May be a single object or a list
        items = data if isinstance(data, list) else [data]
        for item in items:
            offers = item.get("offers", [])
            if isinstance(offers, dict):
                offers = [offers]
            for offer in offers:
                avail = offer.get("availability", "")
                name  = offer.get("name") or offer.get("sku") or "unknown"
                if "InStock" in avail:
                    available.append(name)

    # 2) Fallback: Shopify product JSON embedded in page JS
    if not available:
        m = re.search(r'"variants"\s*:\s*(\[.*?\])', content, re.DOTALL)
        if m:
            try:
                variants = json.loads(m.group(1))
                for v in variants:
                    if v.get("available"):
                        available.append(v.get("title", "unknown variant"))
            except json.JSONDecodeError:
                pass

    return bool(available), available


def send_notification(variants: list[str]) -> None:
    if not NTFY_TOPIC:
        print("  [ntfy] Skipped — NTFY_TOPIC not set.")
        return

    body = "Available: " + ", ".join(variants)
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=body.encode(),
        headers={
            "Title": "Flickaway Weekender is back in stock!",
            "Priority": "high",
            "Tags": "shopping_bags",
            "Click": PRODUCT_URL,
        },
        method="POST",
    )
    _opener.open(req, timeout=10)
    print(f"  [ntfy] Notification sent to topic '{NTFY_TOPIC}'")


def main() -> None:
    print(f"Flickaway stock checker started.")
    print(f"  Product : {PRODUCT_URL}")
    print(f"  Interval: every {CHECK_INTERVAL_MINUTES} min")
    print(f"  ntfy topic: {NTFY_TOPIC or '(not configured)'}")
    print()

    notified = False  # only notify once per restock event

    while True:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] Checking...", end=" ", flush=True)

        try:
            in_stock, variants = check_stock()
        except Exception as exc:
            print(f"ERROR: {exc}")
            time.sleep(CHECK_INTERVAL_MINUTES * 60)
            continue

        if in_stock:
            print(f"IN STOCK! {variants}")
            if not notified:
                send_notification(variants)
                notified = True
        else:
            print("Sold out.")
            notified = False  # reset so we notify again after the next restock

        time.sleep(CHECK_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
