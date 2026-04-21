#!/usr/bin/env python3
"""Dump page HTML to stdout for selector inspection."""
import sys
from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv) > 1 else "https://saveyourdeals.com/deals"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    print(page.content())
    browser.close()
