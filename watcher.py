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
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

# ── SSL context (Bloomberg proxy does SSL inspection) ────────────────────────
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
_WIDTH = 68

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


# ── Notification ─────────────────────────────────────────────────────────────
def send_notification(title: str, body: str, url: str, topic: str) -> None:
    if not topic:
        print(f"  {_FACE_SNIFF} [ntfy] skipped — ntfy_topic not configured~")
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
    print(f"  {_FACE_SNIFF} [ntfy] pinged '{NTFY_TOPIC}'! ✨")


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
def fetch_content(url: str, selector: str | None, spinner: Spinner) -> str:
    """Return inner HTML of the first element matching selector, or full body."""
    with sync_playwright() as p:
        spinner.update(f"{_FACE_BOOT} booting browser~")
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        spinner.update(f"{_FACE_SNIFF} fetching {url}~")
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        spinner.update(f"{_FACE_READ} reading page~")
        if selector:
            el = page.query_selector(selector)
            html = el.inner_html() if el else ""
        else:
            html = page.content()
        browser.close()
    return html


# ── Check logic ───────────────────────────────────────────────────────────────
def check_new_content(name: str, html: str, state: dict) -> list[str]:
    """
    Parse all <a> tags from html, return list of new item identifiers
    (text::href) not seen before. Updates state[name] in place.
    """
    from html.parser import HTMLParser

    class _LinkParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links: list[str] = []
            self._current_href: str = ""

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                attrs_dict = dict(attrs)
                self._current_href = attrs_dict.get("href", "")

        def handle_data(self, data):
            text = data.strip()
            if text and self._current_href:
                self.links.append(f"{text}::{self._current_href}")
                self._current_href = ""

    parser = _LinkParser()
    parser.feed(html)
    current = parser.links

    seen: list[str] = state.get(name, [])
    seen_set = set(seen)
    new_items = [item for item in current if item not in seen_set]

    # Merge: keep seen + add new (preserve order)
    state[name] = seen + new_items
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

    while True:
        import datetime
        state = load_state()

        for checker in checkers:
            name     = checker["name"]
            url      = checker["url"]
            kind     = checker["type"]
            selector = checker.get("selector")
            title    = checker.get("notify_title", f"{name} changed!")
            now      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            spinner.start(f"{_FACE_BOOT} [{name}] starting~")
            try:
                html = fetch_content(url, selector, spinner)
            except Exception as exc:
                spinner.stop(f"[{now}] {_FACE_ERROR} [{name}] fetch failed: {exc}")
                continue

            spinner.update(f"{_FACE_CRUNCH} [{name}] crunching~")

            if kind == "new_content":
                new_items = check_new_content(name, html, state)
                if new_items:
                    labels = [item.split("::")[0] for item in new_items]
                    body = "New: " + ", ".join(labels)
                    spinner.stop(f"[{now}] {_FACE_ALERT} [{name}] new! {body}")
                    send_notification(title, body, url, ntfy_topic)
                else:
                    spinner.stop(f"[{now}] {_FACE_CALM} [{name}] nothing new~")

            elif kind == "change":
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


if __name__ == "__main__":
    main()
