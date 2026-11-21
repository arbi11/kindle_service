"""Medium creator feeds and native, sanitized Freedium article imports."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import re
import hashlib
from urllib.parse import parse_qsl, urlencode, unquote, urljoin, urlsplit, urlunsplit

import requests

from connection_sdk import MAX_FEED_BYTES, iso_date, parse_feed
from .content import plain_text, sanitize
from .feeds import PublicFeedHTTP, Subscription, feed_root, paid_preview, page_is_paid, public_url
from .store import now

FREEDIUM_HOSTS = {"freedium-mirror.cfd", "freedium.cfd"}
FREEDIUM_ORIGIN = "https://freedium-mirror.cfd"
HANDLE = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}"
POST_ID = re.compile(r"(?:/p/|[-/])([a-f0-9]{12})/?$", re.IGNORECASE)


def creator_feed(value):
    """Canonicalize handles and the two Medium profile URL forms to one feed."""
    value = str(value).strip()
    if re.fullmatch("@?" + HANDLE, value) and not value.lower().endswith(".medium.com"):
        handle = value.lstrip("@")
    else:
        parsed = urlsplit(public_url(value))
        host, path = parsed.hostname, parsed.path.rstrip("/")
        if host in ("medium.com", "www.medium.com"):
            match = re.fullmatch(r"/(?:feed/)?@(" + HANDLE + ")", path)
            if not match:
                raise ValueError("Use a Medium creator's @handle or profile URL, not an article or publication")
            handle = match.group(1)
        elif host.endswith(".medium.com") and path in ("", "/feed"):
            handle = host[:-len(".medium.com")]
            if not re.fullmatch(HANDLE, handle) or "." in handle:
                raise ValueError("Enter a Medium @handle or creator profile URL")
        else:
            raise ValueError("Use a Medium @handle, medium.com/@name or name.medium.com profile")
    return "https://medium.com/feed/@" + handle.lower()


def creator_list(value):
    if not isinstance(value, str) or len(value) > 12000:
        raise ValueError("Add up to 20 Medium creators, one per line")
    entries = [part.strip() for part in value.splitlines() if part.strip()]
    if not 1 <= len(entries) <= 20:
        raise ValueError("Add 1–20 Medium creators, one @handle or profile URL per line")
    return list(dict.fromkeys(creator_feed(part) for part in entries))


def article_address(value):
    """Keep the original link, but identify a post independently of its hostname."""
    address = public_url(value)
    # Accept a Freedium link copied from a browser as well as the Medium original.
    for _ in range(3):
        parsed = urlsplit(address)
        if parsed.hostname not in FREEDIUM_HOSTS:
            break
        address = public_url(unquote(parsed.path.lstrip("/")))
    parsed = urlsplit(address)
    if parsed.hostname in FREEDIUM_HOSTS:
        raise ValueError("Paste the original Medium article URL")
    match = POST_ID.search(parsed.path)
    if not match:
        raise ValueError("Paste a Medium article link ending in its 12-character story ID")
    post_id = match.group(1).lower()
    original = urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))
    return {"id": post_id, "external_id": "medium:" + post_id, "url": original,
            "medium_url": "https://medium.com/p/" + post_id}


def saved_article(store, value):
    """Recognize older Raindrop/EPUB imports before the stable ID was introduced."""
    address = article_address(value)
    possible = store.query("SELECT id,external_id,url FROM items WHERE kind='article' AND (external_id=? OR lower(url) LIKE ?)",
                           (address["external_id"], "%" + address["id"] + "%"))
    for item in possible:
        if item["external_id"] == address["external_id"]:
            return item["id"]
        try:
            if article_address(item["url"])["id"] == address["id"]:
                return item["id"]
        except ValueError:
            continue
    return None


def freedium_address(value):
    """Accept publication articles as well as the existing Medium URL forms."""
    address = public_url(value)
    for _ in range(3):
        parsed = urlsplit(address)
        if parsed.hostname not in FREEDIUM_HOSTS:
            break
        address = public_url(unquote(parsed.path.lstrip("/")))
    parsed = urlsplit(address)
    if parsed.hostname in FREEDIUM_HOSTS or not parsed.path.strip("/"):
        raise ValueError("Paste the original article URL")
    try:
        return article_address(address)
    except ValueError:
        if parsed.hostname == "medium.com" or parsed.hostname.endswith(".medium.com"):
            raise
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid")]
    address = urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), urlencode(query), ""))
    return {"external_id": "publication:" + hashlib.sha256(address.encode()).hexdigest(),
            "url": address, "medium_url": address}


def medium_entries(payload, feed_url):
    root = feed_root(payload)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError("Medium did not return a creator RSS feed")
    title = (channel.findtext("title") or "Medium creator").strip()
    title = re.sub(r"^Stories by (.+) on Medium$", r"\1", title)
    domains = set()
    for node in channel.findall("item/link"):
        try:
            domains.add(urlsplit(public_url(node.text or "")).hostname)
        except ValueError:
            continue
    entries = parse_feed(payload, sorted(domains), fallback_date=now(), include_content=True,
                         default_author=title, base_url=feed_url)
    result = []
    for item in entries:
        try:
            address = article_address(item.url)
        except ValueError:
            continue  # Responses and non-story feed links are not library articles.
        result.append(replace(item, external_id=address["external_id"], url=address["url"]))
    return title[:200], result


def resolve_creator(value):
    feed_url = creator_feed(value)
    response = PublicFeedHTTP().get(feed_url, limit=MAX_FEED_BYTES,
                                   allowed_hosts={"medium.com", "www.medium.com"})
    title, _ = medium_entries(response.content, feed_url)
    return {"title": title, "url": feed_url,
            "detail": {"profile_url": feed_url.replace("/feed/", "/"), "freedium_fallback": True}}


def feed_is_preview(body):
    return (not body or len(plain_text(body).strip()) < 80 or paid_preview(body)
            or bool(re.search(r"medium-feed-snippet|member-only story|become a member to read", body, re.I)))


def clean_body(body, base):
    from lxml import html
    doc = html.fragment_fromstring(body or "", create_parent="div")
    for link in doc.iter("a"):
        link.set("href", urljoin(base, link.get("href", "")))
    result = sanitize(html.tostring(doc, encoding="unicode"))
    if len(plain_text(result).strip()) < 80:
        raise ValueError("No readable article text was returned")
    return result


def fetch_freedium(value):
    """Extract article text, never execute or frame the third-party website."""
    from lxml import html
    address = freedium_address(value)
    target = FREEDIUM_ORIGIN + "/" + address["medium_url"]
    try:
        response = PublicFeedHTTP().get(target, timeout=30, allowed_hosts=FREEDIUM_HOSTS)
        doc = html.fromstring(response.content)
        articles = doc.xpath('//article')
        if not articles:
            raise ValueError("Freedium did not return an article")
        article = articles[0]
        titles = article.xpath('.//h1')
        bodies = article.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " prose ")]'
                               ' | .//*[contains(concat(" ", normalize-space(@class), " "), " main-content ")]')
        if not titles or not bodies:
            raise ValueError("Freedium did not return readable article content")
        title = titles[0].text_content().strip()
        body = clean_body(html.tostring(bodies[0], encoding="unicode"), address["url"])
        if feed_is_preview(body):
            raise ValueError("Freedium returned an excerpt")
        headers = article.xpath('.//header')
        header = " ".join(" ".join(headers[0].itertext()).split()) if headers else ""
        author = re.search(r"\bBy\s+(.+?)\s+\d+\s*min(?:ute)?s?\s*read\b", header, re.I)
        dates = doc.xpath('//meta[@property="article:published_time"]/@content | //time/@datetime')
        published = iso_date(dates[0], "") if dates else ""
        if not published:
            date_match = re.search(r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b", header)
            if date_match:
                try:
                    published = datetime.strptime(date_match.group(1), "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    pass
        # Keep the original author's link, not the Freedium wrapper, in the library.
        for link in article.xpath('.//header//a/@href'):
            try:
                original = freedium_address(urljoin(address["url"], link))
                if original["external_id"] == address["external_id"]:
                    address["url"] = original["url"]
                    break
            except ValueError:
                continue
        return {"body_html": body, "title": title, "author": author.group(1).strip() if author else "",
                "url": address["url"], "published": published or now(),
                "content_source": "freedium", "content_url": target}
    except (requests.RequestException, ValueError, html.etree.ParserError) as exc:
        raise ValueError("Freedium could not provide this article. It has not been saved as a full article; try again later.") from exc


class Medium(Subscription):
    def discover(self, session, source, since, *, token=""):
        response = self.http(session).get(source["url"], limit=MAX_FEED_BYTES,
                                          allowed_hosts={"medium.com", "www.medium.com"})
        _, entries = medium_entries(response.content, source["url"])
        entries.sort(key=lambda item: item.published, reverse=True)
        # Inspect the whole bounded feed on later runs: a missed run or backlog
        # must not lose a post simply because it is older than the lookback.
        return entries if source.get("checked_at") else entries[:3]

    def read_article(self, candidate):
        from lxml import html
        body = (candidate.detail or {}).get("feed_html", "")
        preview = feed_is_preview(body) if body else False
        try:
            response = self.http(None).get(candidate.url)
            if not html.fromstring(response.content).xpath('//article'):
                raise ValueError("Medium returned a sign-in or non-article page")
            if not page_is_paid(response.content):
                if body and not preview:
                    return {"body_html": clean_body(body, candidate.url), "title": candidate.title,
                            "author": candidate.author, "url": candidate.url,
                            "content_source": "medium", "content_url": candidate.url}
                import trafilatura
                extracted = trafilatura.extract(response.content, url=candidate.url, output_format="html",
                                               include_links=True, include_formatting=True,
                                               include_tables=True, include_comments=False) or ""
                return {"body_html": clean_body(extracted, candidate.url), "title": candidate.title,
                        "author": candidate.author, "url": candidate.url,
                        "content_source": "medium", "content_url": candidate.url}
        except (requests.RequestException, ValueError):
            # A full RSS content element remains readable when the page blocks
            # automated requests. Explicit feed excerpts always need a resolver.
            if body and not preview:
                return {"body_html": clean_body(body, candidate.url), "title": candidate.title,
                        "author": candidate.author, "url": candidate.url,
                        "content_source": "medium", "content_url": candidate.url}
        return fetch_freedium(candidate.url)
