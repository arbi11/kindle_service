"""Desktop persistence, permissions, source processing, scheduling and HTTP tests.

External network and real email are forbidden. SMTP/AI are exercised through mocks.
"""
import base64
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import smtplib
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile
from xml.etree import ElementTree as ET

from desktop.content import sanitize, render_newsletter
from desktop.engine import Engine
from desktop.secrets import Secrets
from desktop.server import Application, validate_settings
from desktop.store import Store, now
from epub_writer import write_epub


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "data")
        self.secrets = Secrets(self.store.root, use_keyring=False)
        self.engine = Engine(self.store, self.secrets)
        self.external = patch("requests.sessions.Session.request", side_effect=AssertionError("External network forbidden"))
        self.external.start()
        self.mail = patch("smtplib.SMTP_SSL", side_effect=AssertionError("Real SMTP forbidden"))
        self.mail.start()

    def tearDown(self):
        self.engine.close()
        self.mail.stop()
        self.external.stop()
        self.temporary.cleanup()

    def add_piece(self, **extra):
        return self.store.add_item(kind="article", external_id=extra.pop("external_id", "one"), title="A useful article",
                                   url="https://example.com/article", body_html="<p>" + "Good reading. " * 100 + "</p>", **extra)

    def edition(self):
        item = self.add_piece()
        return self.store.make_edition("Today's edition & a good read", [item])

    def configured_delivery(self):
        self.store.save_settings({"email_enabled": True, "email_to": "reader@example.com", "kindle_enabled": True,
            "kindle_to": "reader@kindle.com", "smtp_username": "sender@example.com", "smtp_sender": "sender@example.com"})
        self.secrets.set("smtp_password", "synthetic-test-password")

    def test_credentials_private_and_never_in_database_or_status(self):
        self.secrets.set("openai_key", "sk-synthetic-sensitive-value")
        self.assertEqual(self.secrets.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.secrets.get("openai_key"), "sk-synthetic-sensitive-value")
        self.assertNotIn("sk-synthetic-sensitive-value", json.dumps(self.secrets.status()))
        self.assertNotIn(b"sk-synthetic-sensitive-value", self.store.path.read_bytes())
        self.assertNotIn("sk-synthetic-sensitive-value", self.secrets.redact("Bad key sk-synthetic-sensitive-value"))
        self.secrets.set("openai_key", "")
        self.assertFalse(self.secrets.get("openai_key"))

    def test_reading_state_and_source_dedup_survive_restart(self):
        source = self.store.add_source("article", "Example", "https://example.com/article")
        self.assertEqual(source, self.store.add_source("article", "Again", "https://example.com/article"))
        item = self.add_piece(source_id=source)
        self.assertEqual(item, self.add_piece(source_id=source))
        self.store.execute("UPDATE items SET is_read=1,starred=1 WHERE id=?", (item,))
        reopened = Store(self.store.root)
        self.assertEqual(len(reopened.items(status="saved")), 1)
        self.assertEqual(len(reopened.items(status="unread")), 0)
        self.assertEqual(len(reopened.items(search="useful")), 1)

    def test_fresh_app_does_not_unlock_an_empty_os_wallet(self):
        backend = MagicMock()
        self.secrets.keyring = backend
        self.assertEqual(self.secrets.get("openai_key"), "")
        backend.get_password.assert_not_called()
        self.secrets.set("openai_key", "sk-synthetic-wallet-value")
        backend.set_password.assert_called_once()
        self.secrets.cache.clear()
        backend.get_password.return_value = "sk-synthetic-wallet-value"
        self.assertEqual(self.secrets.get("openai_key"), "sk-synthetic-wallet-value")
        backend.get_password.assert_called_once()

    def test_active_content_and_external_images_are_removed(self):
        content = sanitize('<script>fetch("/api/settings")</script><p onclick="bad()">Text <a href="javascript:bad()">bad</a>'
                           '<a href="https://example.com">good</a><img src="https://tracker.example/pixel"></p><iframe src="x"></iframe>')
        for forbidden in ("script", "onclick", "javascript:", "tracker.example", "iframe"):
            self.assertNotIn(forbidden, content)
        self.assertIn('rel="noopener noreferrer"', content)
        self.assertIn("Text", content)
        self.assertEqual(sanitize(""), "")

    def test_epub_has_valid_xml_complete_manifest_and_uncompressed_first_mimetype(self):
        path = write_epub(self.root / "test.epub", "Numbers & caveats", [("A < B", '<h1>A &amp; B</h1><p>A $3 million loss.<br>Not a guarantee.</p>')])
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.infolist()[0].filename, "mimetype")
            self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)
            for name in archive.namelist():
                if name.endswith((".xml", ".opf", ".ncx", ".xhtml")):
                    ET.fromstring(archive.read(name))
            opf = ET.fromstring(archive.read("OEBPS/content.opf"))
            manifest = {x.attrib["id"]: x.attrib["href"] for x in opf.findall(".//{*}manifest/{*}item")}
            for href in manifest.values():
                self.assertIn("OEBPS/" + href, archive.namelist())
            for ref in opf.findall(".//{*}spine/{*}itemref"):
                self.assertIn(ref.attrib["idref"], manifest)
            self.assertIn(b"$3 million", archive.read("OEBPS/chapter-1.xhtml"))

    def test_newsletter_has_full_text_and_escapes_titles(self):
        edition = self.store.edition(self.edition())
        html = render_newsletter(edition)
        self.assertIn("Good reading.", html)
        self.assertIn("&amp;", html)
        self.assertIn("Read the source", html)

    def test_email_and_kindle_have_correct_bodies_and_deduplicate(self):
        self.configured_delivery()
        edition_id = self.edition()
        job = self.store.create_job("send")
        smtp = MagicMock()
        with patch.object(self.engine, "smtp_connection", return_value=smtp):
            result = self.engine.job_send(job, {"edition_id": edition_id})
            messages = [call.args[0] for call in smtp.__enter__.return_value.send_message.call_args_list]
            self.assertEqual(len(messages), 2)
            email = next(m for m in messages if m["To"] == "reader@example.com")
            kindle = next(m for m in messages if m["To"] == "reader@kindle.com")
            self.assertEqual(len(list(email.iter_attachments())), 0)
            self.assertIn("Good reading.", email.get_body(preferencelist=("html",)).get_content())
            self.assertEqual(len(list(kindle.iter_attachments())), 1)
            self.assertEqual(next(kindle.iter_attachments()).get_content_type(), "application/epub+zip")
            self.engine.job_send(job, {"edition_id": edition_id})
            self.assertEqual(smtp.__enter__.return_value.send_message.call_count, 2)
        self.assertIn("2 recipient", result["message"])
        self.assertEqual(self.store.query("SELECT status FROM editions WHERE id=?", (edition_id,), one=True)["status"], "sent")

    def test_failed_signin_can_retry_but_uncertain_sends_do_not_repeat(self):
        self.configured_delivery()
        self.store.save_settings({"kindle_enabled": False})
        edition_id, job = self.edition(), self.store.create_job("send")
        with patch.object(self.engine, "smtp_connection", side_effect=ValueError("Bad password")):
            with self.assertRaises(ValueError):
                self.engine.job_send(job, {"edition_id": edition_id})
        self.assertEqual(self.store.query("SELECT status FROM deliveries", one=True)["status"], "failed")
        smtp = MagicMock()
        smtp.__enter__.return_value.send_message.side_effect = TimeoutError("Connection lost during send")
        with patch.object(self.engine, "smtp_connection", return_value=smtp):
            with self.assertRaises(ValueError):
                self.engine.job_send(job, {"edition_id": edition_id})
            with self.assertRaisesRegex(ValueError, "resolve this delivery"):
                self.engine.job_send(job, {"edition_id": edition_id})
            self.assertEqual(smtp.__enter__.return_value.send_message.call_count, 1)
        self.assertEqual(self.store.query("SELECT status FROM deliveries ORDER BY created_at DESC", one=True)["status"], "uncertain")

    def test_preparation_extracts_articles_without_ai_and_never_sends(self):
        self.store.save_settings({"summarize_articles": False})
        self.store.add_source("article", "Example", "https://example.com/article")
        def extract(session, article, *_):
            article.title = "A useful article"
            article.body_html = "<p>" + "Useful reading. " * 100 + "</p>"
            return article
        with patch("desktop.engine.rk.fetch_article", side_effect=extract), patch.object(self.engine, "job_send", side_effect=AssertionError("Mail forbidden")):
            first = self.engine.job_prepare(self.store.create_job("prepare"), {})
            second = self.engine.job_prepare(self.store.create_job("prepare"), {})
        self.assertEqual(first["added"], 1)
        item = self.store.items()[0]
        self.assertEqual(item["summary_html"], "")
        self.assertGreater(item["words"], 100)
        self.assertTrue(first["edition_id"])
        self.assertEqual(second["added"], 0)
        self.assertIsNone(second["edition_id"])
        self.assertEqual(len(self.store.query("SELECT * FROM editions")), 1)

    def test_blocked_articles_are_not_marked_known_and_fail_clearly(self):
        self.store.add_source("article", "Example", "https://example.com/article")
        with patch("desktop.engine.rk.fetch_article", side_effect=lambda session,article,*_:article):
            with self.assertRaisesRegex(ValueError, "could not be extracted"):
                self.engine.job_prepare(self.store.create_job("prepare"), {})
        self.assertFalse(self.store.known("article", "https://example.com/article"))
        self.assertIn("could not be extracted", self.store.sources()[0]["error"])

    def test_failed_items_also_consume_the_processing_budget(self):
        self.store.save_settings({"max_items_per_run": 2})
        for index in range(5):
            self.store.add_source("article", f"Example {index}", f"https://example.com/{index}")
        with patch("desktop.engine.rk.fetch_article", side_effect=lambda session,article,*_:article) as extract:
            with self.assertRaises(ValueError):
                self.engine.job_prepare(self.store.create_job("prepare"), {})
            self.assertEqual(extract.call_count, 2)

    def test_uploaded_epub_worker_cannot_delete_an_arbitrary_local_file(self):
        private = self.root / "keep.txt"
        private.write_text("keep this file")
        with self.assertRaisesRegex(ValueError, "file picker"):
            self.engine.job_import_epub(self.store.create_job("import_epub"), {"path": str(private)})
        self.assertEqual(private.read_text(), "keep this file")

    def test_import_epub_is_local_safe_and_idempotent(self):
        path = write_epub(self.root / "old.epub", "An old edition", [("Old reading", "<p>" + "An old good read. " * 20 + "</p>")])
        edition = self.engine.import_epub(path)
        self.assertTrue(edition)
        self.assertIsNone(self.engine.import_epub(path))
        self.assertEqual(len(self.store.edition(edition)["items"]), 1)
        self.assertEqual(self.store.edition(edition)["status"], "imported")

    def test_missing_key_never_picks_up_unrelated_environment_or_legacy_key(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-unrelated-key"}):
            self.assertEqual(ai_key(self.engine.config()), "")

    def test_scheduler_collapses_missed_slots_and_records_only_success(self):
        self.store.save_settings({"schedule_enabled": True, "delivery_times": ["08:00", "14:00"], "timezone": "UTC"})
        current = datetime(2026, 11, 19, 17, tzinfo=timezone.utc)
        with patch.object(self.engine, "job_prepare", return_value={"message": "Done"}) as prepare:
            self.engine.check_schedule(current)
            self.engine.executor.submit(lambda: None).result(timeout=3)
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(self.store.settings()["last_schedule_slot"], "2026-11-19T14:00:00+00:00")
            self.engine.check_schedule(current)
            self.assertEqual(prepare.call_count, 1)

    def test_invalid_settings_are_rejected(self):
        for data in ({"delivery_times": ["99:00"]}, {"timezone": "Not/AZone"}, {"smtp_sender": "x\r\nBcc: other@example.com"},
                     {"reading_minutes": -10}, {"provider": "unknown"}, {"schedule_enabled": "yes"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_settings(data)


def ai_key(config):
    import ai_client
    return ai_client.api_key(config["shared"])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.temporary.name), use_keyring=False, scheduler=False)
        self.cookie = "edition_session=" + self.app.token

    def tearDown(self):
        self.app.close()
        self.temporary.cleanup()

    def request(self, path, method="GET", body=None, auth=True, origin=None, headers=None):
        base = {"Content-Type": "application/json", "X-Daily-Edition": "1"}
        if auth:
            base["Cookie"] = self.cookie
        if origin:
            base["Origin"] = origin
        base.update(headers or {})
        request = Request(self.app.origin + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=base)
        return urlopen(request, timeout=5)

    def test_api_requires_private_session_and_rejects_cross_origin_mutations(self):
        with self.assertRaises(HTTPError) as denied:
            self.request("/api/bootstrap", auth=False)
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        with self.assertRaises(HTTPError) as denied:
            self.request("/api/settings", "POST", {"settings": {"model": "changed"}}, origin="https://other.example")
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        with self.request("/api/bootstrap") as response:
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_settings_never_return_keys_and_library_state_persists(self):
        with self.request("/api/settings", "POST", {"credentials": {"openai_key": "sk-synthetic-private-value"}, "settings": {"reading_minutes": 20}}) as response:
            result = response.read().decode()
            self.assertNotIn("sk-synthetic-private-value", result)
        with self.request("/api/bootstrap") as response:
            result = json.load(response)
        self.assertEqual(result["settings"]["reading_minutes"], 20)
        self.assertTrue(result["secrets"]["configured"]["openai_key"])
        with self.request("/api/items") as response:
            item = json.load(response)["items"][0]
        with self.request("/api/items/" + item["id"], "PATCH", {"starred": True, "is_read": True}):
            pass
        with self.request("/api/items?status=saved") as response:
            self.assertEqual(len(json.load(response)["items"]), 1)

    def test_export_endpoints_return_actual_epub_and_newsletter(self):
        edition = self.app.store.editions()[0]
        with self.request("/api/editions/" + edition["id"] + "/epub") as response:
            self.assertEqual(response.headers["Content-Type"], "application/epub+zip")
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
        with self.request("/api/editions/" + edition["id"] + "/html") as response:
            self.assertIn(b"DAILY EDITION", response.read())

    def test_enabling_schedule_waits_for_the_next_slot(self):
        with self.request("/api/settings", "POST", {"settings": {"schedule_enabled": True}}):
            pass
        self.assertTrue(self.app.store.settings()["last_schedule_slot"])
        with patch.object(self.app.engine, "submit") as submit:
            self.app.engine.check_schedule()
            submit.assert_not_called()

    def test_static_assets_and_sample_data_load_without_external_requests(self):
        for path in ("/", "/app.js", "/app.css", "/icon.svg"):
            with self.subTest(path=path), self.request(path) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())
        self.assertEqual(len(self.app.store.items()), 3)


if __name__ == "__main__":
    unittest.main()
