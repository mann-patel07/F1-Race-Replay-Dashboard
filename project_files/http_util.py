"""Shared HTTP helper for the Jolpica and Open-Meteo downloads: spacing, timeout, retries, paging."""
import time

import requests

import config


class Http:
    def __init__(self, spacing_s):
        self.spacing_s = spacing_s
        self.last = 0.0
        self.sleep = time.sleep

    def get(self, url):
        wait = config.RETRY_WAIT_S
        last_error = None
        for attempt in range(config.REQUEST_RETRIES + 1):
            gap = self.spacing_s - (time.monotonic() - self.last)
            if gap > 0:
                self.sleep(gap)
            self.last = time.monotonic()
            try:
                resp = requests.get(url, timeout=config.REQUEST_TIMEOUT_S)
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
            else:
                self.last = time.monotonic()
                if resp.status_code == 200:
                    return resp.json()
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code != 429 and resp.status_code < 500:
                    raise RuntimeError(last_error)
            if attempt < config.REQUEST_RETRIES:
                self.sleep(wait)
                wait *= 2
        raise RuntimeError(f"giving up on {url}: {last_error}")


def read_all_pages(fetch, url, extract, page=None):
    """Read limit and offset pages until offset reaches MRData.total.

    fetch(url) returns parsed JSON, extract(json) returns the list of items on that page."""
    page = page or config.JOLPICA_PAGE
    sep = "&" if "?" in url else "?"
    items, offset = [], 0
    while True:
        body = fetch(f"{url}{sep}limit={page}&offset={offset}")
        items.append(extract(body))
        total = int(body["MRData"]["total"])
        offset += page
        if offset >= total:
            return items
