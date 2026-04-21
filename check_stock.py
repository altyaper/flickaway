#!/usr/bin/env python3
"""
Flickaway Weekender stock checker.
Polls the product page and sends a push notification via ntfy.sh when any variant is available.
"""

import itertools
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.request

# Bloomberg's proxy does SSL inspection — use an unverified context for outbound HTTPS
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE
_opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_ctx))

from playwright.sync_api import sync_playwright

# ── Faces ────────────────────────────────────────────────────────────────────
_ACTIVE_FACES = ["(^・ω・^)", "(^-ω-^)", "(^・ω・^)", "(^.ω.^)"]
_FACE_BOOT    = "(^・ω・^)"
_FACE_SNIFF   = "(◕‿◕) "
_FACE_READ    = "( •̀ω•́) "
_FACE_CRUNCH  = "(๑•̀ㅂ•́)"
_FACE_STOCK   = "(★^O^★)"
_FACE_EMPTY   = "(╥_╥)  "
_FACE_ERROR   = "(；￣Д￣)"
_SLEEP_FACES  = ["( -ω-)  ", "(-ω-)   ", "(-ω-)   "]
_ZZZ_FRAMES   = ["z      ", "zZ     ", "zZZ    ", "ZZz    ", "Zzz    ", " zzZ   "]
_WIDTH = 68


class Spinner:
    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._msg: list[str] = [""]
        self._thread: threading.Thread | None = None

    def start(self, message: str = "") -> None:
        self._msg[0] = message
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def update(self, message: str) -> None:
        self._msg[0] = message

    def stop(self, final: str = "") -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write(f"\r{' ' * _WIDTH}\r")
        if final:
            print(final)
        sys.stdout.flush()

    def _spin(self) -> None:
        for i, dot in enumerate(itertools.cycle(self._FRAMES)):
            if self._stop.is_set():
                break
            face = _ACTIVE_FACES[(i // 4) % len(_ACTIVE_FACES)]
            line = f"{dot} {face} {self._msg[0]}"
            sys.stdout.write(f"\r{line:<{_WIDTH}}")
            sys.stdout.flush()
            time.sleep(0.08)


def animated_sleep(total_seconds: int) -> None:
    """Sleeping pet animation with ZzZ and countdown."""
    start = time.time()
    for i in itertools.count():
        elapsed = time.time() - start
        remaining = total_seconds - elapsed
        if remaining <= 0:
            break
        mins, secs = divmod(int(remaining), 60)
        face = _SLEEP_FACES[(i // 6) % len(_SLEEP_FACES)]
        zzz  = _ZZZ_FRAMES[i % len(_ZZZ_FRAMES)]
        line = f"{face} {zzz}  next sniff in {mins:02d}:{secs:02d}~"
        sys.stdout.write(f"\r{line:<{_WIDTH}}")
        sys.stdout.flush()
        time.sleep(0.35)
    sys.stdout.write(f"\r{' ' * _WIDTH}\r")
    sys.stdout.flush()


PRODUCT_URL = "https://flickaway.bic.com/products/420-flickaway-weekender"
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))

# ntfy.sh config — set NTFY_TOPIC to your chosen topic name (keep it unique/private)
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")


def check_stock(spinner: Spinner | None = None) -> tuple[bool, list[str]]:
    """
    Returns (any_available, list_of_available_variant_titles).
    """
    def _status(msg: str) -> None:
        if spinner:
            spinner.update(msg)

    with sync_playwright() as p:
        _status(f"{_FACE_BOOT} booting up~")
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        _status(f"{_FACE_SNIFF} sniffing the webz~")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30_000)

        _status(f"{_FACE_READ} nom nom reading page~")
        content = page.content()
        browser.close()
        _status(f"{_FACE_CRUNCH} crunching the data~")

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
        print(f"  {_FACE_SNIFF} [ntfy] skipped — NTFY_TOPIC not set~")
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
    print(f"  {_FACE_SNIFF} [ntfy] pinged '{NTFY_TOPIC}'! ✨")


def main() -> None:
    print(f"(^・ω・^) flickaway watcher is awake!")
    print(f"  product  : {PRODUCT_URL}")
    print(f"  interval : every {CHECK_INTERVAL_MINUTES} min")
    print(f"  ntfy     : {NTFY_TOPIC or '(not configured)'}")
    print()

    notified = False
    spinner = Spinner()

    while True:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        spinner.start(f"{_FACE_BOOT} booting up~")
        try:
            in_stock, variants = check_stock(spinner)
        except Exception as exc:
            spinner.stop(f"[{now}] {_FACE_ERROR} ouchie!! {exc}")
            animated_sleep(CHECK_INTERVAL_MINUTES * 60)
            continue

        if in_stock:
            spinner.stop(f"[{now}] {_FACE_STOCK} OMG ITS HERE!! ✨  {', '.join(variants)}")
            if not notified:
                send_notification(variants)
                notified = True
        else:
            spinner.stop(f"[{now}] {_FACE_EMPTY} still sold out... *sniffle*")
            notified = False

        animated_sleep(CHECK_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
