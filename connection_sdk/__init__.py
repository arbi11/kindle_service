"""Version 1 of the connection contract. This module needs only Python's stdlib.

Connections discover candidates; the host owns extraction, summaries, storage,
credentials, and delivery. Community feed definitions contain data, never code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from typing import Protocol
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

API_VERSION = 1
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Candidate:
    external_id: str
    title: str
    url: str
    published: str
    author: str = ""
    kind: str = "article"
    detail: dict | None = None


class Connection(Protocol):
    definition: dict

    def discover(self, session, source: dict, since: datetime, *, token: str = "") -> list[Candidate]: ...


def iso_date(value: str, fallback: str = "") -> str:
    try:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return fallback


def allowed_url(url: str, domains: list[str]) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        return bool(parsed.scheme == "https" and parsed.port in (None, 443)
                    and not parsed.username and not parsed.password and host in domains)
    except (ValueError, TypeError, AttributeError):
        return False


def validate_manifest(data: dict) -> dict:
    required = {"id", "name", "version", "api_version", "adapter", "description", "maintainer",
                "homepage", "icon", "permissions", "setup"}
    if not isinstance(data, dict) or not required.issubset(data):
        raise ValueError("Connection manifest is missing required fields")
    if set(data) - required - {"feed_url", "domains"}:
        raise ValueError("Unknown connection manifest field")
    if not isinstance(data["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,49}", data["id"]):
        raise ValueError("Invalid connection ID")
    if not isinstance(data["version"], str) or not re.fullmatch(r"\d+\.\d+\.\d+", data["version"]):
        raise ValueError("Use a semantic connection version")
    if type(data["api_version"]) is not int or data["api_version"] != API_VERSION:
        raise ValueError("This connection requires a different app connection API")
    for field, length in (("name", 80), ("description", 500), ("maintainer", 100)):
        if not isinstance(data[field], str) or not data[field].strip() or len(data[field]) > length:
            raise ValueError(f"Invalid connection {field}")
    builtins = ("youtube", "raindrop", "substack", "websites", "medium")
    if data["adapter"] not in (*builtins, "rss"):
        raise ValueError("This adapter is not supported")
    if data["adapter"] in builtins and data["adapter"] != data["id"]:
        raise ValueError("Built-in adapters cannot be assigned to another connection")
    if data["icon"] not in ("video", "cloud", "article", "book", "rss"):
        raise ValueError("Choose a supported connection icon")
    homepage = urlparse(str(data["homepage"]))
    if homepage.scheme != "https" or not homepage.hostname or homepage.username or homepage.password:
        raise ValueError("A connection homepage must use HTTPS")
    if not isinstance(data["permissions"], list) or not 1 <= len(data["permissions"]) <= 8 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 200 for item in data["permissions"]):
        raise ValueError("Describe the connection's access")
    if data["setup"] not in (*builtins, "feed"):
        raise ValueError("Unsupported connection setup")
    if data["adapter"] == "rss":
        domains = data.get("domains")
        if not isinstance(domains, list) or not 1 <= len(domains) <= 10:
            raise ValueError("A feed connection must declare its domains")
        for domain in domains:
            if not isinstance(domain, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain) or "." not in domain:
                raise ValueError("Use explicit public domain names, without wildcards")
            try:
                ipaddress.ip_address(domain)
            except ValueError:
                pass
            else:
                raise ValueError("IP addresses are not permitted in connection definitions")
            if domain.endswith((".local", ".localhost", ".internal")):
                raise ValueError("Local domains are not permitted")
        if data["setup"] != "feed" or not allowed_url(data.get("feed_url", ""), domains):
            raise ValueError("The feed URL must use HTTPS on a declared domain")
    elif data["setup"] != data["adapter"] or "feed_url" in data or "domains" in data:
        raise ValueError("Invalid built-in connection configuration")
    return data


def load_catalogue(folder: Path) -> dict[str, dict]:
    """Load only the versions explicitly pinned by the reviewed index."""
    folder = folder.resolve()
    index = json.loads((folder / "catalogue.json").read_text())
    if not isinstance(index, dict) or index.get("api_version") != API_VERSION or not isinstance(index.get("connections"), list):
        raise ValueError("Invalid connection catalogue")
    result = {}
    for entry in index["connections"]:
        if not isinstance(entry, dict) or set(entry) != {"id", "manifest", "sha256"}:
            raise ValueError("Invalid catalogue entry")
        path = (folder / entry["manifest"]).resolve()
        if not path.is_relative_to(folder) or path.name != "connection.json":
            raise ValueError("Invalid connection manifest path")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"Connection {entry['id']} does not match its reviewed version")
        definition = validate_manifest(json.loads(raw))
        if definition["id"] != entry["id"] or definition["id"] in result:
            raise ValueError("Duplicate or mismatched connection ID")
        result[definition["id"]] = definition
    return result


def parse_feed(payload: bytes, domains: list[str], *, fallback_date: str, include_content=False,
               default_author="", base_url="") -> list[Candidate]:
    """Read bounded RSS 2.0 or Atom. Ignore entries outside the declared hosts."""
    declarations = payload.replace(b"\x00", b"").upper()
    if len(payload) > MAX_FEED_BYTES or b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise ValueError("This feed is too large or contains unsupported XML declarations")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise ValueError("The service did not return a readable feed") from error
    if root.tag == "rss":
        entries, atom = root.findall("./channel/item"), False
    elif root.tag == "{http://www.w3.org/2005/Atom}feed":
        entries, atom = root.findall("{*}entry"), True
    else:
        raise ValueError("This connection requires an RSS or Atom feed")
    result, seen = [], set()
    for entry in entries[:500]:
        if atom:
            links = entry.findall("{*}link")
            url = next((link.get("href", "") for link in links if link.get("rel", "alternate") == "alternate"), "").strip()
            title = "".join(entry.find("{*}title").itertext()).strip() if entry.find("{*}title") is not None else ""
            published = entry.findtext("{*}published") or entry.findtext("{*}updated") or ""
            author = entry.findtext("{*}author/{*}name") or ""
            content = entry.find("{*}content")
            if content is not None and content.get("type") == "xhtml":
                from copy import deepcopy
                children = [deepcopy(child) for child in content]
                for child in children:
                    for node in child.iter():
                        node.tag = node.tag.split("}")[-1]
                body = "".join(ET.tostring(child, encoding="unicode") for child in children)
            elif content is not None and content.get("type", "text") == "text":
                from html import escape
                body = "<p>" + escape(content.text or "") + "</p>"
            else:
                body = (content.text or "") if content is not None else ""
        else:
            url = (entry.findtext("link") or "").strip()
            title = (entry.findtext("title") or "").strip()
            published = entry.findtext("pubDate") or entry.findtext("{*}date") or ""
            author = entry.findtext("author") or entry.findtext("{*}creator") or ""
            body = entry.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or ""
        if base_url:
            url = urljoin(base_url, url)
            parsed = urlparse(url)
            if parsed.scheme == "http":
                url = urlunparse(parsed._replace(scheme="https"))
        if not title or not allowed_url(url, domains) or url in seen:
            continue
        seen.add(url)
        detail = {"feed_html": body} if include_content and body else None
        result.append(Candidate(url, title[:1000], url, iso_date(published, fallback_date),
                                (author or default_author)[:300], detail=detail))
    return result
