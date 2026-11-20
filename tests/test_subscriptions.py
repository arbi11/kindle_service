"""Public publication subscriptions: discovery, extraction and network limits."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from desktop.connections import Catalogue
from desktop.engine import Engine
from desktop.feeds import (PublicFeedHTTP, PublicHTTPSAdapter, feed_info, paid_preview,
                           public_url, resolve_subscription)
from desktop.secrets import Secrets
from desktop.store import Store, now
from test_connections import article, response

SUBSTACK_SITE = "https://letters.example/"
BLOG_SITE = "https://blog.example/"
TEXT = "A complete public article with concrete numbers, useful context and caveats. " * 12


def publication_page(substack=True, feed="/feed"):
    return ('<html><head><title>A publication</title>' +
            ('<meta name="generator" content="Substack">' if substack else '') +
            f'<link rel="alternate" type="application/rss+xml" href="{feed}"></head><body></body></html>').encode()


def publication_feed(site=SUBSTACK_SITE, *, paid=False, old=False, full=True):
    date = datetime.now(timezone.utc) - timedelta(days=60 if old else 0)
    body = f'<p>{TEXT}</p><script>window.feedWasExecuted=true</script><img src="https://tracking.example/pixel">'
    if paid:
        body = '<p>This post is for paid subscribers. Subscribe to continue reading.</p>'
    content = f'<content:encoded><![CDATA[{body}]]></content:encoded>' if full else '<description>A short preview.</description>'
    return (f'<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
            f'<channel><title>Useful Letters</title><link>{site}</link>'
            f'<item><title>Public reading</title><link>{site}p/public-reading</link>'
            f'<pubDate>{date.strftime("%a, %d %b %Y %H:%M:%S GMT")}</pubDate>{content}'
            '</item></channel></rss>').encode()


def blog_feed():
    return (f'<feed xmlns="http://www.w3.org/2005/Atom"><title>A Thoughtful Blog</title>'
            f'<link href="{BLOG_SITE}"/><entry><title>A readable blog post</title>'
            f'<link href="{BLOG_SITE}a-readable-post"/><updated>{now()}</updated>'
            f'<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>{TEXT}</p>'
            '<p><a href="/reference">A relative reference</a></p></div></content></entry></feed>').encode()


def publication_request(method, url, **kwargs):
    if url == SUBSTACK_SITE:
        return response(url, publication_page())
    if url == SUBSTACK_SITE + "feed":
        return response(url, publication_feed())
    if url == BLOG_SITE:
        return response(url, publication_page(False, "/atom.xml"))
    if url == BLOG_SITE + "atom.xml":
        return response(url, blog_feed())
    if url.startswith((SUBSTACK_SITE, BLOG_SITE)):
        return response(url, article())
    raise AssertionError("Unexpected network access: " + url)


class PublicTransportTests(unittest.TestCase):
    def test_addresses_reject_credentials_local_networks_ports_and_non_https(self):
        self.assertEqual(public_url("blog.example/feed#top"), "https://blog.example/feed")
        for value in ("", "http://blog.example", "file:///etc/passwd", "https://localhost/",
                      "https://10.0.0.1", "https://127.0.0.1", "https://[::1]", "https://host.local",
                      "https://user:pass@blog.example", "https://blog.example:8080/feed",
                      "https://blog.example\\@127.0.0.1", "https://blog.example/\nprivate"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                public_url(value)

    def test_dns_is_pinned_with_original_tls_hostname_and_private_answers_are_blocked(self):
        adapter = PublicHTTPSAdapter()
        request = requests.Request("GET", "https://blog.example/feed").prepare()
        for address in ("127.0.0.1", "10.0.0.2", "192.168.1.3", "169.254.169.254", "100.64.0.1", "224.0.0.1", "::1", "fc00::1"):
            with self.subTest(address=address), patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (address, 443))]), self.assertRaisesRegex(ValueError, "private networks"):
                adapter.get_connection_with_tls_context(request, True)
        adapter.poolmanager = MagicMock()
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            adapter.get_connection_with_tls_context(request, True)
        args = adapter.poolmanager.connection_from_host.call_args.kwargs
        self.assertEqual(args["host"], "93.184.216.34")
        self.assertEqual(args["pool_kwargs"]["assert_hostname"], "blog.example")
        self.assertEqual(args["pool_kwargs"]["server_hostname"], "blog.example")
        self.assertEqual(request.headers["Host"], "blog.example")

    def test_redirect_and_document_limits_apply_before_fetching_private_target(self):
        with patch("requests.sessions.Session.request", return_value=response(BLOG_SITE, status=302,
                   headers={"Location": "https://127.0.0.1/private"})) as fetch:
            with self.assertRaises(ValueError):
                PublicFeedHTTP().get(BLOG_SITE)
            self.assertEqual(fetch.call_count, 1)
        with patch("requests.sessions.Session.request", return_value=response(BLOG_SITE, b"x" * 101)):
            with self.assertRaisesRegex(ValueError, "too large"):
                PublicFeedHTTP().get(BLOG_SITE, limit=100)


class SubscriptionTests(unittest.TestCase):
    def test_custom_domain_substack_and_advertised_atom_feed_discovery(self):
        with patch("requests.sessions.Session.request", side_effect=publication_request):
            substack = resolve_subscription(SUBSTACK_SITE, "substack")
            blog = resolve_subscription(BLOG_SITE, "websites")
            direct = resolve_subscription(BLOG_SITE + "atom.xml", "websites")
        self.assertEqual(substack["url"], SUBSTACK_SITE + "feed")
        self.assertEqual(substack["title"], "Useful Letters")
        self.assertEqual(blog["url"], BLOG_SITE + "atom.xml")
        self.assertEqual(blog, direct)

    def test_substack_rejects_a_generic_website_and_website_explains_missing_feeds(self):
        with patch("requests.sessions.Session.request", side_effect=publication_request):
            with self.assertRaisesRegex(ValueError, "Substack publication"):
                resolve_subscription(BLOG_SITE, "substack")
        with patch("requests.sessions.Session.request", return_value=response(BLOG_SITE, b"<html>No feed here</html>")):
            with self.assertRaisesRegex(ValueError, "No RSS or Atom"):
                resolve_subscription(BLOG_SITE, "websites")

    def test_atom_xhtml_keeps_paragraphs_and_rss_uses_publisher_content_not_description(self):
        _, _, entries = feed_info(blog_feed(), BLOG_SITE + "atom.xml")
        body = entries[0].detail["feed_html"]
        self.assertIn("<p>", body)
        self.assertIn(TEXT, body)
        self.assertNotIn("html:", body)
        _, _, entries = feed_info(publication_feed(full=False), SUBSTACK_SITE + "feed")
        self.assertIsNone(entries[0].detail)

    def test_legacy_http_feed_metadata_and_article_links_are_upgraded_to_https(self):
        payload = blog_feed().replace(b'https://blog.example/', b'http://blog.example/')
        _, site, entries = feed_info(payload, BLOG_SITE + "atom.xml")
        self.assertEqual(site, BLOG_SITE)
        self.assertEqual(entries[0].url, BLOG_SITE + "a-readable-post")

    def test_quiet_publications_get_initial_posts_then_respect_lookback(self):
        subscription = Catalogue().get("substack")
        source = {"url": SUBSTACK_SITE + "feed", "checked_at": ""}
        with patch("requests.sessions.Session.request", return_value=response(source["url"], publication_feed(old=True))):
            self.assertEqual(len(subscription.discover(None, source, datetime.now(timezone.utc) - timedelta(days=3))), 1)
            source["checked_at"] = now()
            self.assertEqual(subscription.discover(None, source, datetime.now(timezone.utc) - timedelta(days=3)), [])

    def test_paid_previews_are_not_imported_as_full_articles(self):
        subscription = Catalogue().get("substack")
        self.assertTrue(paid_preview('<p>A teaser. Read more</p>', substack=True))
        self.assertFalse(paid_preview('<p>Subscribe now to support this free essay.</p>'))
        with patch("requests.sessions.Session.request", return_value=response(SUBSTACK_SITE + "feed", publication_feed(paid=True))):
            self.assertEqual(subscription.discover(None, {"url": SUBSTACK_SITE + "feed"}, datetime.now(timezone.utc)), [])
        _, _, entries = feed_info(publication_feed(full=False), SUBSTACK_SITE + "feed")
        locked = b'<html><head><script type="application/ld+json">{"@type":"Article","isAccessibleForFree":false}</script></head><body><article>A paid preview</article></body></html>'
        with patch("requests.sessions.Session.request", return_value=response(entries[0].url, locked)):
            with self.assertRaisesRegex(ValueError, "paid preview"):
                subscription.read_article(entries[0])

    def test_short_feed_uses_article_extraction_and_relative_links_are_resolved(self):
        subscription = Catalogue().get("websites")
        _, _, entries = feed_info(publication_feed(BLOG_SITE, full=False), BLOG_SITE + "feed")
        with patch("requests.sessions.Session.request", return_value=response(entries[0].url, article())) as fetch:
            extracted = subscription.read_article(entries[0])
        self.assertIn("$3 million", extracted["body_html"])
        self.assertEqual(fetch.call_count, 1)
        _, _, entries = feed_info(blog_feed(), BLOG_SITE + "atom.xml")
        with patch("requests.sessions.Session.request", side_effect=AssertionError("Full feed should not fetch a page")):
            self.assertIn('href="https://blog.example/reference"', subscription.read_article(entries[0])["body_html"])

    def test_follow_fetch_export_repeat_and_disconnect_preserve_local_reading_without_ai(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary))
            engine = Engine(store, Secrets(store.root, use_keyring=False))
            try:
                with patch("requests.sessions.Session.request", side_effect=publication_request), \
                     patch("ai_client.chat", side_effect=AssertionError("AI not configured")), \
                     patch.object(engine, "job_send", side_effect=AssertionError("Mail forbidden")):
                    first = engine.job_add_subscription(store.create_job("add_subscription"), {"kind": "substack", "url": SUBSTACK_SITE})
                    again = engine.job_add_subscription(store.create_job("add_subscription"), {"kind": "substack", "url": SUBSTACK_SITE + "feed"})
                    self.assertEqual(first["source_id"], again["source_id"])
                    payload = {"source_id": first["source_id"]}
                    prepared = engine.job_prepare(store.create_job("prepare"), payload)
                    repeated = engine.job_prepare(store.create_job("prepare"), payload)
                self.assertEqual(prepared["added"], 1)
                self.assertEqual(repeated["added"], 0)
                item = store.items()[0]
                self.assertEqual(item["connection_id"], "substack")
                self.assertIn(TEXT, item["body_html"])
                self.assertNotIn("script", item["body_html"])
                self.assertNotIn("tracking.example", item["body_html"])
                self.assertTrue(engine.export_epub(prepared["edition_id"]).exists())
                store.execute("DELETE FROM sources WHERE id=?", (first["source_id"],))
                self.assertEqual(store.items()[0]["connection_id"], "substack")
                self.assertEqual(store.query("SELECT count(*) AS n FROM deliveries", one=True)["n"], 0)
            finally:
                engine.close()
