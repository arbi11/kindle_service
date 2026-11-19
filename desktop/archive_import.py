"""One-time, offline copy of the original services' retained reading.

Only books and delivery metadata are read. No configuration, credentials,
subscriptions, network clients, mail clients, or background watchers are used.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
import posixpath
import re
from urllib.parse import parse_qs, urldefrag, urlparse
from zoneinfo import ZoneInfo
import zipfile
import io

from connection_sdk import iso_date
from .content import plain_text, sanitize
from .store import now, private_write, uid


@dataclass(frozen=True)
class Story:
    connection: str
    kind: str
    external_id: str
    title: str
    url: str
    author: str
    published: str
    content: str
    takeaway: str = ""
    summary_missing: bool = False


@dataclass(frozen=True)
class Edition:
    connection: str
    path: Path
    digest: str
    payload: bytes
    date: str
    delivery_recorded: bool
    stories: tuple[Story, ...]

    @property
    def id(self):
        return f"legacy-{self.connection}-{self.digest[:24]}"


def _member(parent, href):
    name = posixpath.normpath(posixpath.join(posixpath.dirname(parent), href))
    if name.startswith(("../", "/")) or name == "..":
        raise ValueError("The EPUB contains an invalid chapter path")
    return name


def _book_date(path, history, zone):
    recorded = iso_date(history.get("slot", ""))
    if recorded:
        return recorded
    modified = datetime.fromtimestamp(path.stat().st_mtime, zone)
    match = re.match(r"([A-Za-z]{3}\d{2})_", path.name)
    if match:
        date = datetime.strptime(str(modified.year) + match[1], "%Y%b%d").replace(tzinfo=zone)
        if date.date() > modified.date():
            date = date.replace(year=date.year - 1)
        return date.astimezone(timezone.utc).isoformat()
    return modified.astimezone(timezone.utc).isoformat()


def _published(parts, video, date):
    recorded = iso_date(video.get("published", ""))
    if recorded:
        return recorded
    for part in reversed(parts[1:]):
        value = iso_date(part)
        if value:
            return value
    if len(parts) > 1:
        try:
            reference = datetime.fromisoformat(date)
            value = datetime.strptime(f"{reference.year} {parts[1]}", "%Y %b %d").replace(tzinfo=reference.tzinfo)
            if value > reference:
                value = value.replace(year=value.year - 1)
            return value.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return date


def _stories(payload, connection, date, videos):
    from lxml import etree, html

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 60 * 1024 * 1024:
            raise ValueError("The uncompressed EPUB is too large")
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        container = etree.fromstring(archive.read("META-INF/container.xml"), parser)
        opf_path = _member("", container.find(".//{*}rootfile").get("full-path"))
        opf = etree.fromstring(archive.read(opf_path), parser)
        manifest = {entry.get("id"): entry for entry in opf.findall(".//{*}manifest/{*}item")}
        overview = []
        for ref in opf.findall(".//{*}spine/{*}itemref"):
            entry = manifest.get(ref.get("idref"))
            if entry is None or "nav" in (entry.get("properties") or "").split():
                continue
            if entry.get("media-type") not in ("application/xhtml+xml", "text/html"):
                continue
            doc = html.fromstring(archive.read(_member(opf_path, entry.get("href", ""))))
            links = doc.xpath('//p[contains(concat(" ",normalize-space(@class)," ")," source ")]/a[@href]')
            if not links:
                if connection == "youtube":  # The first chapter is an edition overview.
                    overview.extend(node.text_content().strip() for node in doc.xpath("//li"))
                    continue
                raise ValueError("An article chapter has no source URL")
            url = urldefrag(links[0].get("href"))[0]
            if urlparse(url).scheme not in ("http", "https"):
                raise ValueError("An article chapter has an invalid source URL")
            headings = doc.xpath("//h1")
            if not headings:
                raise ValueError("An article chapter has no title")
            title = headings[0].text_content().strip()
            metadata = doc.xpath('//p[contains(concat(" ",normalize-space(@class)," ")," meta ")]')
            parts = metadata[0].text_content().split(" · ") if metadata else []
            parsed = urlparse(url)
            video_id = parsed.path.strip("/") if parsed.hostname == "youtu.be" else parse_qs(parsed.query).get("v", [""])[0]
            if connection == "youtube" and (parsed.hostname not in ("youtube.com", "www.youtube.com", "youtu.be") or not re.fullmatch(r"[\w-]+", video_id)):
                raise ValueError("A video chapter has an invalid YouTube URL")
            video = videos.get(video_id, {}) if connection == "youtube" else {}
            published = _published(parts, video, date)
            author_parts = [part for part in parts if not iso_date(part)]
            author = parts[0] if connection == "youtube" and parts else " · ".join(author_parts)
            author = author or video.get("channel") or parsed.hostname or connection
            prefix = f"{author}: {title}. "
            takeaway = next((line[len(prefix):] for line in overview if line.startswith(prefix)), "")
            source_paragraph = links[0].getparent()
            for heading in headings:
                if heading.text_content().strip() == title:
                    heading.drop_tree()
            source_paragraph.drop_tree()
            if metadata:
                metadata[0].drop_tree()
            body = doc.find(".//body")
            content = sanitize(html.tostring(body if body is not None else doc, encoding="unicode"))
            summary_missing = not plain_text(content).strip()
            if summary_missing:
                content = (f"<p>{escape(takeaway)}</p>" if takeaway else "")
                content += "<p><em>The saved Kindle edition contains no summary text for this video.</em></p>" if connection == "youtube" else "<p><em>The saved Kindle edition contains no article text. Use Open source to read it.</em></p>"
            external_id = video_id if connection == "youtube" else "legacy-raindrop:" + hashlib.sha256(url.encode()).hexdigest()
            yield Story(connection, "video" if connection == "youtube" else "article", external_id,
                        title, url, author, published, content, takeaway, summary_missing)


def read_archive(state_root: Path, timezone_name="Asia/Kolkata"):
    """Snapshot retained sent books, including books older than the 50-entry log."""
    editions, skipped = [], []
    zone = ZoneInfo(timezone_name)
    for connection, service in (("raindrop", "raindrop-kindle"), ("youtube", "youtube-kindle")):
        root = Path(state_root) / service
        if not (root / "state.json").exists():
            continue
        state = json.loads((root / "state.json").read_text())
        history = {row["file"]: row for row in state.get("history", []) if row.get("file")}
        dates = [iso_date(row.get("slot", "")) for row in history.values()]
        oldest = min((date for date in dates if date), default="")
        for path in sorted((root / "books").glob("*.epub")):
            record = history.get(path.name, {})
            date = _book_date(path, record, zone)
            if not record and not (len(history) >= 50 and oldest and date < oldest):
                skipped.append({"connection": connection, "file": path.name, "reason": "Not present in delivery history"})
                continue
            if path.stat().st_size > 25 * 1024 * 1024:
                raise ValueError("A retained EPUB is too large: " + path.name)
            payload = path.read_bytes()
            stories = tuple(_stories(payload, connection, date, state.get("videos", {})))
            if not stories:
                raise ValueError("A retained EPUB has no readable articles: " + path.name)
            editions.append(Edition(connection, path, hashlib.sha256(payload).hexdigest(), payload, date, bool(record), stories))
    editions.sort(key=lambda edition: edition.date)
    return editions, skipped


def import_archive(store, editions):
    """Copy the snapshots and atomically add their reading without changing settings."""
    folder = store.root / "reading-archive"
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    for edition in editions:
        destination = folder / (edition.id + ".epub")
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != edition.digest:
                raise ValueError("An existing archive copy has changed")
        else:
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(edition.payload)
            temporary.chmod(0o600)
            temporary.replace(destination)
    inserted, missing_text, added_editions = set(), set(), 0
    zone = ZoneInfo(store.settings()["timezone"])
    with store.lock, store.connect() as db:
        for edition in editions:
            if db.execute("SELECT 1 FROM editions WHERE id=?", (edition.id,)).fetchone():
                continue
            date = datetime.fromisoformat(edition.date).astimezone(zone)
            title = f"{'YouTube' if edition.connection == 'youtube' else 'Raindrop'} · {date:%d %b %Y, %H:%M}"
            db.execute("INSERT INTO editions(id,title,created_at,status) VALUES(?,?,?,'imported')", (edition.id, title, edition.date))
            added_editions += 1
            ids = []
            for story in edition.stories:
                existing = db.execute("SELECT * FROM items WHERE kind=? AND (external_id=? OR (connection_id=? AND url=?)) LIMIT 1",
                                      (story.kind, story.external_id, story.connection, story.url)).fetchone()
                words = len(plain_text(story.content).split())
                if existing:
                    item_id = existing["id"]
                    # Preserve the most complete retained version, and all reading/bookmark state.
                    if item_id in inserted and not story.summary_missing and (item_id in missing_text or words > existing["words"]):
                        column = "summary_html" if story.kind == "video" else "body_html"
                        db.execute(f"UPDATE items SET {column}=?,words=? WHERE id=?", (story.content, words, item_id))
                        missing_text.discard(item_id)
                else:
                    item_id = uid()
                    db.execute("""INSERT INTO items(id,kind,external_id,title,url,author,published,body_html,summary_html,
                               words,created_at,connection_id,takeaway) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (item_id, story.kind, story.external_id, story.title, story.url, story.author, story.published,
                                story.content if story.kind == "article" else "", story.content if story.kind == "video" else "",
                                words, now(), story.connection, story.takeaway))
                    inserted.add(item_id)
                    if story.summary_missing:
                        missing_text.add(item_id)
                if item_id not in ids:
                    ids.append(item_id)
            db.executemany("INSERT INTO edition_items VALUES(?,?,?)", [(edition.id, item_id, position) for position, item_id in enumerate(ids)])
    manifest = [{"connection": edition.connection, "original_filename": edition.path.name, "sha256": edition.digest,
                 "reader_edition_id": edition.id, "date": edition.date, "delivery_record_retained": edition.delivery_recorded}
                for edition in editions]
    private_write(folder / "manifest.json", json.dumps(manifest, indent=2))
    return {"editions_added": added_editions, "stories_added": len(inserted), "original_epubs_copied": len(editions),
            "stories_without_saved_text": len(missing_text)}
