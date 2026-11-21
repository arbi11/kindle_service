"""Isolated browser-test server. Service responses are synthetic; mail is blocked."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sys
import threading
from unittest.mock import patch
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from desktop.server import Application
from desktop.store import now
from test_connections import LINK, article, response, rss
from test_subscriptions import BLOG_SITE, SUBSTACK_SITE, publication_request
from test_medium import network as medium_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--medium-demo", action="store_true")
    args = parser.parse_args()
    original = requests.sessions.Session.request

    def fake_request(session, method, url, **kwargs):
        host, path = urlparse(url).hostname, urlparse(url).path
        if host in ("127.0.0.1", "localhost"):
            return original(session, method, url, **kwargs)
        if url == "https://freedium-mirror.cfd/rss":
            return response(url, rss())
        if host == "medium.com" or url.startswith("https://freedium-mirror.cfd/https://medium.com/"):
            return medium_request(method, url, **kwargs)
        if url == LINK or host == "reading.example":
            return response(url, article())
        if url.startswith((BLOG_SITE, SUBSTACK_SITE)):
            return publication_request(method, url, **kwargs)
        if host == "api.raindrop.io":
            if path.endswith("/collections"):
                value = {"items": [{"_id": 3, "title": "Science"}]}
            else:
                value = {"items": [{"_id": 100, "link": "https://reading.example/saved-article", "title": "Saved reading fixture",
                                    "created": now(), "tags": ["science"]}]}
            return response(url, json.dumps(value).encode())
        raise AssertionError("External network is disabled in this UI test: " + str(host))

    channel = {"id": "UCsample1234567890123456", "name": "Sample channel", "handle": "@sample"}
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    with patch("requests.sessions.Session.request", new=fake_request), \
         patch("youtube_kindle.resolve_channel", return_value=channel), \
         patch("smtplib.SMTP_SSL", side_effect=AssertionError("Mail disabled in UI tests")), \
         patch("smtplib.SMTP", side_effect=AssertionError("Mail disabled in UI tests")):
        app = Application(args.data_dir, use_keyring=False, scheduler=False)
        if args.medium_demo:
            app.store.save_settings({"onboarding_done": True, "schedule_enabled": False,
                                     "auto_send": False, "summarize_articles": False})
        try:
            print("Connection UI fixture service ready.", flush=True)
            stopped.wait()
        finally:
            app.close()


if __name__ == "__main__":
    main()
