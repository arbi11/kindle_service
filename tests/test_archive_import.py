"""Offline history import: copied content, provenance, duplicates and delivery boundaries."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from desktop.archive_import import import_archive, read_archive
from desktop.store import Store
from epub_writer import write_epub


class ArchiveImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "original"
        self.store = Store(self.root / "reader")
        self.history = {"youtube": [], "raindrop": []}

    def tearDown(self):
        self.temporary.cleanup()

    def book(self, connection, name, *, video="sample-video", text="The loss was $3 million; the hedge was $2 million.", sent=True):
        service = "youtube-kindle" if connection == "youtube" else "raindrop-kindle"
        directory = self.state / service
        url = "https://www.youtube.com/watch?v=" + video if connection == "youtube" else "https://example.org/article#section"
        author = "Example channel" if connection == "youtube" else "Example site · Example author"
        title = "The original story"
        body = f'<h1>{title}</h1><p class="meta">{author} · Oct 19</p><p class="source"><a href="{url}">Source</a></p>'
        if text:
            body += f'<p>{text}</p><script>throw new Error("untrusted")</script>'
        chapters = [(title, body)]
        if connection == "youtube":
            chapters.insert(0, ("Overview", '<h1>Today’s videos</h1><ul><li><b>Example channel</b>: The original story. Keep the numbers distinct.</li></ul>'))
        path = write_epub(directory / "books" / name, name, chapters)
        if sent:
            self.history[connection].append({"file": name, "slot": "2026-10-20T08:00:00+05:30", "sent_at": "2026-10-20T08:10:00+05:30"})
        (directory / "state.json").write_text(json.dumps({"history": self.history[connection], "videos": {video: {"published": "2026-10-19T12:00:00+00:00"}}}))
        return path

    def test_copy_is_independent_complete_deduplicated_and_preserves_reader_state(self):
        self.book("raindrop", "Oct20_M_article.epub")
        self.book("raindrop", "Oct20_E_same_article.epub")
        self.book("raindrop", "Oct20_N_failed_send.epub", sent=False)
        self.book("youtube", "Oct20_YT_M_summary.epub", text="")
        self.book("youtube", "Oct20_YT_E_longer_summary.epub")
        originals = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.state.rglob("*") if path.is_file()}
        self.store.save_settings({"model": "reader-choice", "interests": "Existing interests"})
        settings = self.store.settings()
        plan, skipped = read_archive(self.state)
        self.assertEqual(len(plan), 4)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(sum(story.summary_missing for edition in plan for story in edition.stories), 1)
        result = import_archive(self.store, plan)
        self.assertEqual(result["stories_added"], 2)
        self.assertEqual(result["editions_added"], 4)
        self.assertEqual(self.store.settings(), settings)
        self.assertEqual(self.store.sources(), [])
        stories = self.store.items()
        self.assertEqual({item["connection_id"] for item in stories}, {"youtube", "raindrop"})
        video = next(item for item in stories if item["kind"] == "video")
        article = next(item for item in stories if item["kind"] == "article")
        self.assertEqual(video["external_id"], "sample-video")
        self.assertEqual(video["author"], "Example channel")
        self.assertEqual(video["takeaway"], "Keep the numbers distinct.")
        self.assertIn("$3 million", video["summary_html"])
        self.assertNotIn("untrusted", video["summary_html"])
        self.assertNotIn("no summary text", video["summary_html"])
        self.assertNotIn("class=\"source\"", video["summary_html"])
        self.assertIn("Example author", article["author"])
        self.assertEqual(article["url"], "https://example.org/article")
        self.assertEqual(video["published"], "2026-10-19T12:00:00+00:00")
        self.store.execute("UPDATE items SET is_read=1,starred=1 WHERE id=?", (video["id"],))
        repeated = import_archive(self.store, plan)
        self.assertEqual(repeated["stories_added"], 0)
        self.assertEqual(repeated["editions_added"], 0)
        self.assertEqual(len(self.store.items(status="saved")), 1)
        self.assertEqual(originals, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in originals})
        for edition in plan:
            copy = self.store.root / "reading-archive" / (edition.id + ".epub")
            self.assertEqual(hashlib.sha256(copy.read_bytes()).hexdigest(), edition.digest)
            self.assertEqual(copy.stat().st_mode & 0o777, 0o600)
        shutil.rmtree(self.state)
        self.assertIn("$3 million", self.store.items()[0]["summary_html"] or self.store.items()[0]["body_html"])
        self.assertFalse((self.store.root / "credentials.json").exists())
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM deliveries", one=True)["n"], 0)

    def test_books_older_than_truncated_history_are_kept_but_new_unsent_books_are_excluded(self):
        self.book("youtube", "Oct10_YT_D_old.epub", sent=False)
        self.book("youtube", "Nov19_YT_M_not_sent.epub", sent=False)
        history = [{"file": f"retained-{i}.epub", "slot": "2026-11-01T08:00:00+05:30"} for i in range(50)]
        (self.state / "youtube-kindle/state.json").write_text(json.dumps({"history": history}))
        for path in (self.state / "youtube-kindle/books").glob("*.epub"):
            import os
            timestamp = datetime(2026, 11, 19, 12, tzinfo=timezone.utc).timestamp()
            os.utime(path, (timestamp, timestamp))
        plan, skipped = read_archive(self.state)
        self.assertEqual([edition.path.name for edition in plan], ["Oct10_YT_D_old.epub"])
        self.assertFalse(plan[0].delivery_recorded)
        self.assertEqual([row["file"] for row in skipped], ["Nov19_YT_M_not_sent.epub"])

    def test_empty_saved_summary_is_explicit_and_keeps_the_original_takeaway(self):
        self.book("youtube", "Oct20_YT_M_missing.epub", text="")
        plan, _ = read_archive(self.state)
        import_archive(self.store, plan)
        video = self.store.items()[0]
        self.assertIn("no summary text", video["summary_html"])
        self.assertIn("Keep the numbers distinct.", video["summary_html"])

    def test_archive_cannot_escape_zip_member_paths(self):
        path = self.book("raindrop", "Oct20_M_bad_path.epub")
        with zipfile.ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        members["OEBPS/content.opf"] = members["OEBPS/content.opf"].replace(b"chapter-1.xhtml", b"../../escape.xhtml")
        with zipfile.ZipFile(path, "w") as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        with self.assertRaisesRegex(ValueError, "invalid chapter path"):
            read_archive(self.state)
        self.assertEqual(self.store.items(), [])

    def test_imported_bookmark_url_deduplication_is_scoped_to_its_connection(self):
        self.book("raindrop", "Oct20_M_article.epub")
        plan, _ = read_archive(self.state)
        import_archive(self.store, plan)
        self.assertTrue(self.store.known("article", "a-new-bookmark-id", "raindrop", "https://example.org/article#new-section"))
        self.assertFalse(self.store.known("article", "a-new-bookmark-id", "freedium", "https://example.org/article"))
