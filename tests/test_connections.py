"""Connection contracts, feed transport, migration, and host integration.

All remote content below is synthetic. No external network or email is used.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import requests

from connection_sdk import allowed_url, load_catalogue, parse_feed, validate_manifest
from desktop.connections import CATALOGUE, Catalogue, FeedHTTP
from desktop.engine import Engine
from desktop.secrets import Secrets
from desktop.store import Store, now

DOMAIN = "freedium-mirror.cfd"
LINK = "https://" + DOMAIN + "/https://medium.com/@sample/a-careful-explanation"


def response(url, content=b"", status=200, headers=None):
    value = requests.Response()
    value.url, value.status_code = url, status
    value._content, value._content_consumed = content, True
    value.headers.update(headers or {})
    return value


def rss(link=LINK):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <channel><title>Recently Unlocked</title><item><title>A careful explanation</title>
      <link>{link}</link><guid>one</guid><pubDate>{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>
      <dc:creator>Sample writer</dc:creator></item></channel></rss>'''.encode()


def article():
    paragraphs = "".join(f"<p>Part {index}: A $3 million loss and a $2 million hedge are different figures. "
                         "This synthetic article tests readable extraction, local storage and EPUB generation. "
                         "The caveats and source attribution should remain available to the reader.</p>" for index in range(8))
    return ("<!doctype html><html><head><title>A careful explanation</title></head><body><article>"
            "<h1>A careful explanation</h1>" + paragraphs + "</article></body></html>").encode()


class CatalogueTests(unittest.TestCase):
    def test_reviewed_index_pins_versions_and_does_not_load_unlisted_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "connections"
            shutil.copytree(CATALOGUE, folder)
            (folder / "unreviewed.py").write_text("raise RuntimeError('must never run')")
            self.assertEqual(set(load_catalogue(folder)), {"youtube", "raindrop", "freedium"})
            path = folder / "freedium/connection.json"
            path.write_text(path.read_text().replace("Recently Unlocked", "Changed without a new hash"))
            with self.assertRaisesRegex(ValueError, "reviewed version"):
                load_catalogue(folder)

    def test_another_reviewed_feed_needs_no_core_code_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            definition = json.loads((CATALOGUE / "freedium/connection.json").read_text())
            definition.update(id="sample-science", name="Sample Science", feed_url="https://science.example/rss",
                              domains=["science.example"], homepage="https://science.example/")
            path = folder / "sample-science/connection.json"
            path.parent.mkdir()
            raw = json.dumps(definition).encode()
            path.write_bytes(raw)
            (folder / "catalogue.json").write_text(json.dumps({"api_version": 1, "connections": [
                {"id": "sample-science", "manifest": "sample-science/connection.json", "sha256": hashlib.sha256(raw).hexdigest()}]}))
            catalogue = Catalogue(folder)
            self.assertEqual(catalogue.get("sample-science").definition["adapter"], "rss")
            self.assertEqual(catalogue.public([])[0]["name"], "Sample Science")

    def test_manifest_rejects_unbounded_access_code_and_incompatible_versions(self):
        original = json.loads((CATALOGUE / "freedium/connection.json").read_text())
        for changes in ({"domains": ["*"]}, {"domains": ["127.0.0.1"]}, {"domains": ["host.local"]},
                        {"feed_url": "http://freedium-mirror.cfd/rss"}, {"feed_url": "https://other.example/rss"},
                        {"adapter": "python"}, {"api_version": 2}, {"api_version": True},
                        {"entrypoint": "unsafe.py"}, {"credentials": ["smtp_password"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_manifest({**original, **changes})

    def test_catalogue_paths_cannot_escape_the_reviewed_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "catalogue"
            folder.mkdir()
            (folder / "catalogue.json").write_text(json.dumps({"api_version": 1, "connections": [
                {"id": "escape", "manifest": "../connection.json", "sha256": "x"}]}))
            with self.assertRaisesRegex(ValueError, "manifest path"):
                load_catalogue(folder)


class FeedTests(unittest.TestCase):
    def test_rss_normalizes_dates_attribution_and_drops_duplicate_or_external_links(self):
        xml = rss().replace(b"</channel>", ("<item><title>Duplicate</title><link>" + LINK + "</link></item>"
            "<item><title>Outside</title><link>https://outside.example/article</link></item></channel>").encode())
        candidates = parse_feed(xml, [DOMAIN], fallback_date=now())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].author, "Sample writer")
        self.assertEqual(candidates[0].external_id, LINK)
        self.assertEqual(datetime.fromisoformat(candidates[0].published).utcoffset().total_seconds(), 0)

    def test_atom_is_supported_and_uses_alternate_article_link(self):
        xml = f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Science &amp; detail</title>
        <link rel="self" href="https://{DOMAIN}/api/entry"/><link href="{LINK}"/>
        <updated>2026-11-19T10:00:00+05:30</updated><author><name>A writer</name></author></entry></feed>'''.encode()
        candidate = parse_feed(xml, [DOMAIN], fallback_date=now())[0]
        self.assertEqual(candidate.title, "Science & detail")
        self.assertEqual(candidate.published, "2026-11-19T04:30:00+00:00")
        self.assertEqual(candidate.url, LINK)

    def test_unsafe_xml_and_oversized_feeds_are_rejected(self):
        unsafe = '<!DOCTYPE rss [<!ENTITY x "expanded">]><rss><channel/></rss>'
        for payload in (unsafe.encode(), unsafe.encode("utf-16"), b"x" * (2 * 1024 * 1024 + 1), b"not XML"):
            with self.subTest(prefix=payload[:20]), self.assertRaises(ValueError):
                parse_feed(payload, [DOMAIN], fallback_date=now())

    def test_feed_urls_require_https_and_explicit_hosts_without_embedded_credentials(self):
        self.assertTrue(allowed_url(LINK, [DOMAIN]))
        for url in ("http://" + DOMAIN, "https://" + DOMAIN + ".evil.example/", "https://user:pass@" + DOMAIN,
                    "file:///etc/passwd", "https://127.0.0.1/", "https://" + DOMAIN + ":8443/"):
            with self.subTest(url=url):
                self.assertFalse(allowed_url(url, [DOMAIN]))

    def test_transport_checks_redirect_target_before_requesting_it(self):
        session = MagicMock()
        session.get.return_value = response(LINK, status=302, headers={"Location": "https://outside.example/private"})
        with self.assertRaisesRegex(ValueError, "declared HTTPS domains"):
            FeedHTTP(session, [DOMAIN]).get(LINK)
        self.assertEqual(session.get.call_count, 1)
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    def test_transport_bounds_response_bytes_and_follows_allowed_relative_redirects(self):
        session = MagicMock()
        session.get.side_effect = [response(LINK, status=302, headers={"Location": "/article"}),
                                   response("https://" + DOMAIN + "/article", b"readable")]
        self.assertEqual(FeedHTTP(session, [DOMAIN]).get(LINK).content, b"readable")
        session.get.side_effect = None
        session.get.return_value = response(LINK, b"x" * 100)
        with self.assertRaisesRegex(ValueError, "too large"):
            FeedHTTP(session, [DOMAIN]).get(LINK, limit=50)


class ConnectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        self.secrets = Secrets(self.store.root, use_keyring=False)
        self.engine = Engine(self.store, self.secrets)

    def tearDown(self):
        self.engine.close()
        self.temporary.cleanup()

    def test_freedium_feed_to_reader_and_epub_without_ai_or_mail_and_without_duplicates(self):
        source_id = self.store.add_source("freedium", "Freedium", "https://" + DOMAIN + "/rss")
        def fetch(method, url, **kwargs):
            if url == "https://" + DOMAIN + "/rss":
                return response(url, rss())
            if url == LINK:
                return response(url, article())
            raise AssertionError("Unexpected network request: " + url)
        with patch("requests.sessions.Session.request", side_effect=fetch) as network, \
             patch("ai_client.chat", side_effect=AssertionError("AI not configured")), \
             patch.object(self.engine, "job_send", side_effect=AssertionError("Email forbidden")):
            first = self.engine.job_prepare(self.store.create_job("prepare"), {"source_id": source_id})
            second = self.engine.job_prepare(self.store.create_job("prepare"), {"source_id": source_id})
        self.assertEqual(first["added"], 1)
        self.assertEqual(second["added"], 0)
        self.assertEqual(network.call_count, 3)
        item = self.store.items()[0]
        self.assertEqual(item["connection_id"], "freedium")
        self.assertEqual(item["author"], "Sample writer")
        self.assertIn("$3 million", item["body_html"])
        self.assertEqual(item["summary_html"], "")
        self.assertTrue(self.engine.export_epub(first["edition_id"]).exists())
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM deliveries", one=True)["n"], 0)

    def test_youtube_uses_shared_discovery_and_keeps_transcript_summary_and_provenance(self):
        source_id = self.store.add_source("youtube", "Sample channel", "https://www.youtube.com/channel/sample", {"name": "Sample channel"})
        self.secrets.set("openai_key", "sk-synthetic-test-only")
        video = {"title": "A careful video", "published": now()}
        with patch("desktop.connections.yt.feed_entries", return_value=[("abc123def45", datetime.now(timezone.utc), video)]), \
             patch("desktop.engine.yt.fetch_captions", return_value=([(0, "A careful explanation"), (30, "With concrete numbers")], "en")), \
             patch("desktop.engine.yt.summarise", return_value=SimpleNamespace(paragraphs=["[0:00] An explanation with its caveats."], takeaway="Details matter.")), \
             patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden")):
            result = self.engine.job_prepare(self.store.create_job("prepare"), {"source_id": source_id})
        self.assertEqual(result["added"], 1)
        item = self.store.items()[0]
        self.assertEqual(item["connection_id"], "youtube")
        self.assertIn("concrete numbers", item["body_html"])
        self.assertIn("caveats", item["summary_html"])

    def test_raindrop_only_receives_its_token_and_preserves_collection_tag_filtering(self):
        connector = self.engine.catalogue.get("raindrop")
        source = {"detail": {"collection_id": 3, "tags": ["science"]}}
        bookmarks = [{"_id": 1, "link": "https://example.com/a", "title": "Science", "created": now(), "tags": ["science"]},
                     {"_id": 2, "link": "https://example.com/b", "title": "Other", "created": now(), "tags": ["other"]}]
        with patch("desktop.connections.rk.fetch_new_bookmarks", return_value=bookmarks) as fetch:
            candidates = connector.discover(MagicMock(), source, datetime.now(timezone.utc), token="synthetic-raindrop-token")
        self.assertEqual([item.title for item in candidates], ["Science"])
        self.assertEqual(fetch.call_args.args[1], {"raindrop_token": "synthetic-raindrop-token", "collection_id": 3})

    def test_disconnect_and_old_database_migration_preserve_item_identity_and_state(self):
        source_id = self.store.add_source("freedium", "Freedium", "https://" + DOMAIN + "/rss")
        item_id = self.store.add_item(kind="article", external_id=LINK, title="Kept", source_id=source_id)
        self.store.execute("UPDATE items SET is_read=1,starred=1 WHERE id=?", (item_id,))
        self.store.execute("DROP INDEX items_by_connection")
        self.store.execute("ALTER TABLE items DROP COLUMN connection_id")
        reopened = Store(self.store.root)
        self.assertEqual(reopened.items()[0]["connection_id"], "freedium")
        reopened.execute("DELETE FROM sources WHERE id=?", (source_id,))
        item = reopened.items(status="saved")[0]
        self.assertEqual(item["id"], item_id)
        self.assertEqual(item["is_read"], 1)
        self.assertEqual(item["connection_id"], "freedium")

    def test_unknown_or_paused_sources_cannot_be_fetched(self):
        source_id = self.store.add_source("freedium", "Freedium", "https://" + DOMAIN + "/rss")
        self.store.execute("UPDATE sources SET enabled=0 WHERE id=?", (source_id,))
        for source in (source_id, "missing"):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "enabled connection"):
                self.engine.job_prepare(self.store.create_job("prepare"), {"source_id": source})


if __name__ == "__main__":
    unittest.main()
