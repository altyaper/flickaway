#!/usr/bin/env python3
"""
Config-driven page watcher.
Reads config.json, checks each target on an interval, and sends push
notifications via ntfy.sh when new content appears or a page section changes.
"""

import hashlib
import itertools
import json
import os
import sys
import ssl
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE
_opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_ctx))

# ── Faces ────────────────────────────────────────────────────────────────────
_ACTIVE_FACES = ["(^・ω・^)", "(^-ω-^)", "(^・ω・^)", "(^.ω.^)"]
_FACE_BOOT    = "(^・ω・^)"
_FACE_SNIFF   = "(◕‿◕) "
_FACE_READ    = "( •̀ω•́) "
_FACE_CRUNCH  = "(๑•̀ㅂ•́)"
_FACE_ALERT   = "(★^O^★)"
_FACE_CALM    = "(￣▽￣) "
_FACE_ERROR   = "(；￣Д￣)"
_SLEEP_FACES  = ["( -ω-)  ", "(-ω-)   ", "(-ω-)   "]
_ZZZ_FRAMES   = ["z      ", "zZ     ", "zZZ    ", "ZZz    ", "Zzz    ", " zzZ   "]
try:
    _WIDTH = os.get_terminal_size().columns
except OSError:
    _WIDTH = 120

# ── Config / env ─────────────────────────────────────────────────────────────
STATE_PATH  = Path(os.getenv("WATCHER_STATE", "state.json"))


def _find_config() -> Path:
    if env := os.getenv("WATCHER_CONFIG"):
        return Path(env)
    # Auto-discover: first .json in cwd (excluding state.json) that has "checkers"
    for candidate in sorted(Path(".").glob("*.json")):
        if candidate == STATE_PATH:
            continue
        try:
            data = json.loads(candidate.read_text())
            if "checkers" in data:
                return candidate
        except (json.JSONDecodeError, OSError):
            continue
    sys.exit("no watcher config found — create a .json file with a 'checkers' key")


CONFIG_PATH = _find_config()
NTFY_TOPIC  = os.getenv("NTFY_TOPIC", "")


# ── Spinner ──────────────────────────────────────────────────────────────────
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
            line = final[:_WIDTH]
            sys.stdout.write(f"\r{line:<{_WIDTH}}")
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
        try:
            time.sleep(0.35)
        except KeyboardInterrupt:
            raise
    sys.stdout.write(f"\r{' ' * _WIDTH}\r")
    sys.stdout.flush()


# ── Notification ─────────────────────────────────────────────────────────────
def send_notification(title: str, body: str, url: str, topic: str) -> None:
    if not topic:
        sys.stdout.write(f"\r  {_FACE_SNIFF} [ntfy] skipped — ntfy_topic not configured~{' ' * 10}")
        sys.stdout.flush()
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode(),
        headers={
            "Title": title,
            "Priority": "high",
            "Tags": "bell",
            "Click": url,
        },
        method="POST",
    )
    _opener.open(req, timeout=10)
    sys.stdout.write(f"\r  {_FACE_SNIFF} [ntfy] pinged '{NTFY_TOPIC}'! ✨{' ' * 10}")
    sys.stdout.flush()


# ── Config / state I/O ───────────────────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"config not found: {CONFIG_PATH}")
    with CONFIG_PATH.open() as f:
        return json.load(f)


def load_state() -> dict:
    if STATE_PATH.exists():
        with STATE_PATH.open() as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with STATE_PATH.open("w") as f:
        json.dump(state, f, indent=2)


# ── Page fetch ───────────────────────────────────────────────────────────────
def _clean_amazon_url(raw_url: str) -> str:
    """Strip tracking params (tag, linkCode, linkId, campaignId, ref) from an Amazon URL."""
    parsed = urllib.parse.urlparse(raw_url)
    params = urllib.parse.parse_qs(parsed.query)
    for key in ("tag", "linkCode", "linkId", "campaignId", "ref"):
        params.pop(key, None)
    clean_query = urllib.parse.urlencode(params, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=clean_query))


def fetch_amazon_url(product_path: str, base_url: str, spinner: Spinner) -> str:
    """Visit a product page and return the clean Amazon URL from the 'Go to Deal' link."""
    origin = urllib.parse.urlparse(base_url)
    product_url = f"{origin.scheme}://{origin.netloc}{product_path}"
    spinner.update(f"{_FACE_SNIFF} grabbing amazon link~")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(product_url, wait_until="domcontentloaded", timeout=30_000)
            deal_link = page.query_selector('a:has-text("Go to Deal")')
            if deal_link:
                raw = deal_link.get_attribute("href") or ""
                return _clean_amazon_url(raw)
        except Exception:
            pass
        finally:
            browser.close()
    return ""


def fetch_content(url: str, selector: str | None, spinner: Spinner, wait_until: str = "domcontentloaded") -> str | list[dict]:
    """Return full page HTML, or a list of {text, href} dicts when selector targets <a> tags."""
    with sync_playwright() as p:
        spinner.update(f"{_FACE_BOOT} booting browser~")
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        spinner.update(f"{_FACE_SNIFF} fetching {url}~")
        page.goto(url, wait_until=wait_until, timeout=60_000)
        spinner.update(f"{_FACE_READ} reading page~")
        if selector:
            elements = page.query_selector_all(selector)
            results = []
            for el in elements:
                text = (el.inner_text() or "").strip()
                href = el.get_attribute("href") or ""
                if text:
                    results.append({"text": text, "href": href})
            browser.close()
            return results
        else:
            html = page.content()
            browser.close()
            return html


# ── Check logic ───────────────────────────────────────────────────────────────
def check_new_content(name: str, items: list[dict], state: dict) -> list[dict]:
    """
    Compare fetched items against stored state, return list of new item dicts
    not seen before. Updates state[name] in place.
    """
    seen: list[str] = state.get(name, [])
    seen_set = set(seen)
    new_items = []
    new_keys = []
    for item in items:
        key = f"{item['text']}::{item['href']}"
        if key not in seen_set:
            new_items.append(item)
            new_keys.append(key)

    state[name] = seen + new_keys
    return new_items


def check_change(name: str, html: str, state: dict) -> bool:
    """
    Hash html, return True if it differs from stored hash.
    Updates state[name] in place.
    """
    current_hash = hashlib.sha256(html.encode()).hexdigest()
    previous_hash = state.get(name)
    state[name] = current_hash
    if previous_hash is None:
        return False  # first run — establish baseline silently
    return current_hash != previous_hash


# ── Main loop ─────────────────────────────────────────────────────────────────
def main() -> None:
    config = load_config()
    checkers = config.get("checkers", [])
    interval = int(os.getenv("CHECK_INTERVAL_MINUTES", str(config.get("interval_minutes", 30))))
    ntfy_topic = os.getenv("NTFY_TOPIC") or config.get("ntfy_topic", "")

    print(f"(^・ω・^) page watcher is awake!")
    print(f"  config   : {CONFIG_PATH}")
    print(f"  checkers : {len(checkers)}")
    print(f"  interval : every {interval} min")
    print(f"  ntfy     : {NTFY_TOPIC or '(not configured)'}")
    print()
    for c in checkers:
        print(f"  [{c['type']:12s}] {c['name']} — {c['url']}")
    print()

    spinner = Spinner()

    try:
        while True:
            import datetime
            state = load_state()

            for checker in checkers:
                name       = checker["name"]
                url        = checker["url"]
                kind       = checker["type"]
                selector   = checker.get("selector")
                title      = checker.get("notify_title", f"{name} changed!")
                wait_until = checker.get("wait_until", "domcontentloaded")
                now        = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                spinner.start(f"{_FACE_BOOT} [{name}] starting~")
                try:
                    content = fetch_content(url, selector, spinner, wait_until)
                except Exception as exc:
                    spinner.stop(f"[{now}] {_FACE_ERROR} [{name}] fetch failed: {exc}")
                    continue

                spinner.update(f"{_FACE_CRUNCH} [{name}] crunching~")

                if kind == "new_content":
                    new_items = check_new_content(name, content, state)
                    if new_items:
                        latest_item = new_items[0]
                        amazon = fetch_amazon_url(latest_item["href"], url, spinner)
                        if amazon:
                            spinner.stop(f"[{now}] {_FACE_ALERT} [{name}] {amazon}")
                        else:
                            short_title = latest_item['text'][:100] + "..." if len(latest_item['text']) > 100 else latest_item['text']
                            spinner.stop(f"[{now}] {_FACE_ALERT} [{name}] new! {short_title}")
                        body = f"{latest_item['text']}\n{amazon}" if amazon else latest_item["text"]
                        send_notification(title, body, amazon or url, ntfy_topic)
                    else:
                        latest = state.get(name, [""])[-1].split("::")[0] if state.get(name) else "?"
                        short = latest[:100] + "..." if len(latest) > 100 else latest
                        spinner.stop(f"[{now}] {_FACE_CALM} [{name}] nothing new~ latest: {short}")

                elif kind == "change":
                    html = content if isinstance(content, str) else json.dumps(content)
                    changed = check_change(name, html, state)
                    if changed:
                        spinner.stop(f"[{now}] {_FACE_ALERT} [{name}] page changed!")
                        send_notification(title, "Page content has changed.", url, ntfy_topic)
                    else:
                        spinner.stop(f"[{now}] {_FACE_CALM} [{name}] no change~")

                else:
                    spinner.stop(f"[{now}] {_FACE_ERROR} [{name}] unknown type '{kind}'")

            save_state(state)
            animated_sleep(interval * 60)
    except KeyboardInterrupt:
        sys.stdout.write(f"\r{' ' * _WIDTH}\r")
        print("(￣ω￣) bye bye~")


if __name__ == "__main__":
    main()
