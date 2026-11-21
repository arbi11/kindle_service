"""Generic publication discovery, preference selection and local-session reading."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

from connection_sdk import Candidate, MAX_FEED_BYTES, iso_date, parse_feed
from .content import plain_text
from .feeds import PublicFeedHTTP, Subscription, feed_info, feed_root, public_url, paid_preview
from .medium import clean_body, fetch_freedium, feed_is_preview
from .store import now

MAX_CANDIDATES = 40


def checked_article_text(article, candidate, *, provider="Publication"):
    """Apply the same conservative readability checks used in the live audit."""
    text = plain_text(article.get("body_html", ""))
    title = article.get("title", "").strip()
    if not title or title.casefold() == "untitled":
        raise ValueError(provider + " returned an article without a usable title")
    if len(text.split()) < 200:
        raise ValueError(provider + " returned less than 200 words; the text needs review before import")
    def tokens(value):
        value = re.sub(r"\s+[|—–-]\s+(?:The )?(?:New York Times|NYT|Washington Post|Bloomberg|Reuters|Economist|Financial Times|Medium|The Athletic|Quora|Barron.s).*$", "", value, flags=re.I)
        return re.findall(r"\w+", value.casefold())
    expected, returned = tokens(candidate.title), tokens(title)
    overlap = len(set(expected) & set(returned)) / max(1, min(len(set(expected)), len(set(returned))))
    if overlap < .45 and SequenceMatcher(None, " ".join(expected), " ".join(returned)).ratio() < .55:
        raise ValueError(provider + " returned a different headline; the text needs review before import")
    return article


def canonical_url(value):
    parsed = urlsplit(public_url(value))
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid", "mc_cid", "mc_eid")]
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/") or "/", urlencode(query), ""))


def publication_values(values):
    address = canonical_url(values.get("url", ""))
    title = values.get("title", "")
    preference = values.get("preferences", "")
    maximum = values.get("max_articles", 5)
    fallback = values.get("freedium_fallback", True)
    if not isinstance(title, str) or len(title) > 150:
        raise ValueError("Use a publication name of up to 150 characters")
    if not isinstance(preference, str) or len(preference) > 3000:
        raise ValueError("Keep reading preferences within 3,000 characters")
    if type(maximum) is not int or not 1 <= maximum <= 10:
        raise ValueError("Choose 1–10 articles per fetch")
    if type(fallback) is not bool:
        raise ValueError("Choose whether to try Freedium")
    return {"title": title.strip() or urlsplit(address).hostname.removeprefix("www."), "url": address,
            "detail": {"preferences": preference.strip(), "max_articles": maximum, "freedium_fallback": fallback}}


def same_publication(url, selected):
    host = urlsplit(url).hostname or ""
    domain = urlsplit(selected).hostname
    for prefix in ("www.", "feeds.", "rss.", "feed."):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
            break
    return host == domain or host.endswith("." + domain)


def candidate_url(value):
    return "publication:" + hashlib.sha256(canonical_url(value).encode()).hexdigest()


def page_candidates(payload, address):
    """RSS/Atom first; otherwise collect bounded article links from an index."""
    from lxml import html
    try:
        _, _, entries = feed_info(payload, address)
        if not entries:
            root = feed_root(payload)
            links = [node.get("href") or node.text or "" for node in root.findall('.//item/link') + root.findall('.//{*}entry/{*}link')]
            domains = set()
            for link in links[:500]:
                try:
                    url = public_url(urljoin(address, link))
                    if same_publication(url, address):
                        domains.add(urlsplit(url).hostname)
                except ValueError:
                    pass
            entries = parse_feed(payload, sorted(domains), fallback_date=now(), include_content=True, base_url=address)
        return [replace(item, external_id=candidate_url(item.url), url=canonical_url(item.url))
                for item in entries if same_publication(item.url, address)][:MAX_CANDIDATES]
    except ValueError:
        pass
    try:
        doc = html.fromstring(payload)
    except (ValueError, html.etree.ParserError):
        return []
    candidates = []
    # Structured article records give titles/dates without following every link.
    pending = []
    for text in doc.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            value = json.loads(text)
            pending.extend(value if isinstance(value, list) else [value])
        except (ValueError, TypeError):
            pass
    for processed, record in enumerate(pending):
        if processed >= 200:
            break
        if not isinstance(record, dict):
            continue
        for key in ("@graph", "itemListElement"):
            if isinstance(record.get(key), list):
                pending.extend(record[key][:100])
        if isinstance(record.get("item"), dict):
            pending.append(record["item"])
        if record.get("headline") and isinstance(record.get("url"), str):
            candidates.append((record["url"], str(record["headline"]), str(record.get("description", "")),
                               iso_date(record.get("datePublished", ""), "")))
    for link in doc.xpath('//main//a[@href] | //article//a[@href] | //h2//a[@href] | //h3//a[@href]') or doc.xpath('//a[@href]'):
        title = " ".join(link.text_content().split()) or link.get("aria-label", "")
        parent = next(link.iterancestors("article"), link.getparent())
        description = " ".join(parent.text_content().split())[:700] if parent is not None else ""
        dates = parent.xpath('.//time/@datetime') if parent is not None else []
        candidates.append((link.get("href"), title, description, iso_date(dates[0], "") if dates else ""))
    result, seen = [], set()
    for link, title, description, published in candidates:
        try:
            url = canonical_url(urljoin(address, link))
        except ValueError:
            continue
        parsed = urlsplit(url)
        if (not same_publication(url, address) or url in seen or url == canonical_url(address)
                or len(title.strip()) < 18 or len(title) > 1000 or len(title.split()) < 3
                or len(parsed.path.strip("/")) < 12
                or re.search(r"/(?:login|signin|sign-in|subscribe|account|privacy|terms|search|newsletters?|contact|about)(?:/|$)", parsed.path, re.I)):
            continue
        seen.add(url)
        result.append(Candidate(candidate_url(url), title[:1000], url, published or now(),
                                detail={"description": description[:700], "date_known": bool(published)}))
        if len(result) >= MAX_CANDIDATES:
            break
    return result


def extract_article(payload, address, title="", author="", *, authenticated=False):
    import trafilatura
    from lxml import html
    doc = html.fromstring(payload)
    if doc.xpath('//input[@type="password"]') and not doc.xpath('//article'):
        raise ValueError("This page asks you to sign in. Use Sign in / reconnect.")
    body = trafilatura.extract(payload, url=address, output_format="html", include_links=True,
                               include_formatting=True, include_tables=True, include_comments=False) or ""
    text = plain_text(body).strip()
    if len(text) < 200 or paid_preview(body) or re.search(
            r"(?:verify you are human|checking your browser|enable javascript and cookies|access denied|just a moment)", text[:400], re.I):
        raise ValueError("No complete article was found. Try Sign in / reconnect or Freedium.")
    metadata = trafilatura.extract_metadata(payload, default_url=address)
    if metadata:
        title = metadata.title or title
        author = metadata.author or author
    body_doc = html.fragment_fromstring(body, create_parent="div")
    headings = body_doc.xpath('.//h1')
    if headings and " ".join(headings[0].text_content().split()).casefold() == " ".join(title.split()).casefold():
        headings[0].drop_tree()  # The reader already displays the article title.
        body = html.tostring(body_doc, encoding="unicode")
    return {"body_html": clean_body(body, address), "title": title or address,
            "author": author, "url": address,
            "published": iso_date(metadata.date, "") if metadata and metadata.date else "",
            "content_source": "subscriber" if authenticated else "publisher", "content_url": address}


class Publications(Subscription):
    def discover(self, session, source, since, *, token=""):
        try:
            response = PublicFeedHTTP().get(source["url"])
            entries = page_candidates(response.content, response.url or source["url"])
            # Discover only publisher-declared feeds; do not guess dozens of URLs.
            if not response.content.lstrip().startswith((b"<?xml", b"<rss", b"<feed")):
                from lxml import html
                doc = html.fromstring(response.content)
                feeds = doc.xpath('//link[@rel="alternate" and (contains(@type,"rss") or contains(@type,"atom"))]/@href')
                for feed in feeds[:2]:
                    url = public_url(urljoin(source["url"], feed))
                    if same_publication(url, source["url"]):
                        response = PublicFeedHTTP().get(url, limit=MAX_FEED_BYTES)
                        feed_entries = page_candidates(response.content, url)
                        if feed_entries:
                            entries = feed_entries
                            break
        except (requests.RequestException, ValueError):
            entries = []
        if not entries:
            page = self.browser.request(source["id"], source["url"])
            entries = page_candidates(page["html"].encode(), source["url"])
        if not entries:
            raise ValueError("No article links were found. Try a publication section, latest-stories page or RSS feed URL.")
        return [replace(item, detail={**(item.detail or {}), "publication_id": source["id"],
                                     "freedium_fallback": source["detail"].get("freedium_fallback", True)})
                for item in entries if (item.detail or {}).get("date_known") is False
                or not source.get("checked_at") or datetime.fromisoformat(item.published) >= since]

    def read_article(self, candidate):
        detail = candidate.detail or {}
        errors = []
        # A full feed body can already contain all the subscriber's reading.
        body = detail.get("feed_html", "")
        if body and len(plain_text(body).split()) >= 200 and not feed_is_preview(body):
            return {"body_html": clean_body(body, candidate.url), "title": candidate.title,
                    "author": candidate.author, "url": candidate.url,
                    "content_source": "publisher", "content_url": candidate.url}
        try:
            page = PublicFeedHTTP().get(candidate.url)
            from .feeds import page_is_paid
            if page_is_paid(page.content):
                raise ValueError("Subscriber access is needed")
            return checked_article_text(extract_article(page.content, candidate.url, candidate.title, candidate.author),
                                        candidate, provider="Publisher")
        except (requests.RequestException, ValueError) as exc:
            errors.append(str(exc))
        try:
            page = self.browser.request(detail["publication_id"], candidate.url)
            return checked_article_text(extract_article(page["html"].encode(), candidate.url, candidate.title,
                                                       candidate.author, authenticated=True), candidate, provider="Publication browser")
        except ValueError as exc:
            errors.append(str(exc))
        if detail.get("freedium_fallback", True):
            try:
                article = checked_article_text(fetch_freedium(candidate.url), candidate, provider="Freedium")
                # Some mirror headers show retrieval time. Prefer the date from
                # the publication's own feed or dated index when it is known.
                if candidate.published and detail.get("date_known") is not False:
                    article = {**article, "published": candidate.published}
                return article
            except ValueError as exc:
                errors.append(str(exc))
        raise ValueError("Could not read the complete article. Use Sign in / reconnect or try another article. " + "; ".join(errors[-2:]))


def select_articles(candidates, preferences, maximum, decide):
    """Validate a bounded Pi selection; article URLs stay under host control."""
    if not preferences:
        return candidates[:maximum]
    records = [{"id": str(index), "title": item.title, "description": (item.detail or {}).get("description", "")[:700],
                "url": item.url} for index, item in enumerate(candidates[:MAX_CANDIDATES])]
    if not records:
        return []
    response = decide({"preferences": preferences, "maximum": maximum, "articles": records})
    try:
        selected = response["selected"]
        if not isinstance(selected, list):
            raise ValueError
        result, used = [], set()
        for record in selected:
            index = record.get("id") if isinstance(record, dict) else None
            if not isinstance(index, str) or index not in {record["id"] for record in records}:
                raise ValueError
            if index not in used:
                item = candidates[int(index)]
                reason = record.get("reason", "")
                if not isinstance(reason, str):
                    raise ValueError
                result.append(replace(item, detail={**(item.detail or {}), "selection_reason": reason[:500]}))
                used.add(index)
            if len(result) >= maximum:
                break
        return result
    except (KeyError, TypeError, ValueError, IndexError):
        raise ValueError("Pi returned an invalid article selection. Nothing was imported; try again.") from None
