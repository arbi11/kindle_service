"""Reviewed connection implementations and the host's bounded feed transport."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import raindrop_kindle as rk
import youtube_kindle as yt
from connection_sdk import Candidate, MAX_DOCUMENT_BYTES, MAX_FEED_BYTES, allowed_url, iso_date, load_catalogue, parse_feed
from .store import now

CATALOGUE = Path(__file__).resolve().parent.parent / "connections"


class FeedHTTP:
    def __init__(self, session, domains):
        self.session, self.domains = session, domains

    def get(self, url, timeout=25, *, limit=MAX_DOCUMENT_BYTES):
        for _ in range(6):
            if not allowed_url(url, self.domains):
                raise ValueError("This connection cannot access a URL outside its declared HTTPS domains")
            response = self.session.get(url, timeout=(10, min(timeout, 30)), allow_redirects=False, stream=True)
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                response.raise_for_status()
                content, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("The service returned a document that is too large")
                    content.append(chunk)
                response._content = b"".join(content)
                response._content_consumed = True
                return response
            finally:
                response.close()
        raise ValueError("The service redirected too many times")


class YouTube:
    def __init__(self, definition):
        self.definition = definition

    def discover(self, session, source, since, *, token=""):
        entries = yt.feed_entries(session, source["detail"])
        return [Candidate(video_id, video["title"], "https://www.youtube.com/watch?v=" + video_id,
                          iso_date(video["published"], published.isoformat()), source["title"], "video", video)
                for video_id, published, video in sorted(entries, key=lambda row: row[1])
                if published >= since and video.get("status") != "skipped"]


class Raindrop:
    def __init__(self, definition):
        self.definition = definition

    def discover(self, session, source, since, *, token=""):
        if not token:
            raise ValueError("Connect your Raindrop account to collect saved articles")
        config = {"raindrop_token": token, "collection_id": int(source["detail"].get("collection_id", 0))}
        bookmarks = rk.fetch_new_bookmarks(session, config, {"sent": {}}, since=since)
        tags = source["detail"].get("tags", [])
        return [Candidate(str(bookmark["_id"]), bookmark.get("title", ""), bookmark["link"],
                          iso_date(bookmark.get("created", ""), now()))
                for bookmark in bookmarks if not tags or set(tags).intersection(bookmark.get("tags", []))]


class Feed:
    def __init__(self, definition):
        self.definition = definition

    def http(self, session):
        return FeedHTTP(session, self.definition["domains"])

    def discover(self, session, source, since, *, token=""):
        response = self.http(session).get(self.definition["feed_url"], limit=MAX_FEED_BYTES)
        return [item for item in parse_feed(response.content, self.definition["domains"], fallback_date=now())
                if datetime.fromisoformat(item.published) >= since]


class DirectArticle:
    definition = {"id": "article", "name": "Saved articles", "adapter": "article", "icon": "article"}

    def discover(self, session, source, since, *, token=""):
        return [Candidate(source["url"], "", source["url"], now())]


class Catalogue:
    def __init__(self, folder=CATALOGUE):
        self.definitions = load_catalogue(Path(folder))
        adapters = {"youtube": YouTube, "raindrop": Raindrop, "rss": Feed}
        self.connections = {key: adapters[value["adapter"]](value) for key, value in self.definitions.items()}
        self.connections["article"] = DirectArticle()

    def get(self, connection_id):
        if not isinstance(connection_id, str):
            raise ValueError("Choose a connection from the reviewed catalogue")
        try:
            return self.connections[connection_id]
        except KeyError:
            raise ValueError("This connection is not available in the reviewed catalogue") from None

    def public(self, sources):
        return [{**definition, "reviewed": True,
                 "source_count": sum(source["kind"] == connection_id for source in sources),
                 "enabled_count": sum(source["kind"] == connection_id and bool(source["enabled"]) for source in sources)}
                for connection_id, definition in self.definitions.items()]
