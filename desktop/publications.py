"""Generic publication discovery, preference selection and local-session reading."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
import re
import time
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests

from connection_sdk import Candidate, MAX_FEED_BYTES, iso_date, parse_feed
from .content import plain_text
from .feeds import PublicFeedHTTP, Subscription, feed_info, feed_root, public_url, paid_preview
from .medium import clean_body, fetch_freedium, feed_is_preview
from .publication_search import PublicSearch
from .store import now

MAX_CANDIDATES = 40
MAX_SEARCH_QUERIES = 3
MAX_COPY_PAGES = 4
COPY_SEARCH_SECONDS = 45
MAX_TEXT_REVIEWS = 4


def title_tokens(value):
    value = re.sub(r"\s+[|—–-]\s+(?:The )?(?:New York Times|NYT|Washington Post|Bloomberg|Reuters|Economist|Financial Times|Medium|The Athletic|Quora|Barron.s).*$", "", value, flags=re.I)
    return re.findall(r"\w+", value.casefold())


def titles_match(left, right, *, strict=False):
    left, right = title_tokens(left), title_tokens(right)
    common = len(set(left) & set(right))
    overlap = common / max(1, min(len(set(left)), len(set(right))))
    return (common >= (5 if strict else 1) and overlap >= (.75 if strict else .45)
            or SequenceMatcher(None, " ".join(left), " ".join(right)).ratio() >= (.8 if strict else .55))


def url_headline(url):
    slug = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    words = [word for word in re.split(r"[-_]+", slug) if word and not re.fullmatch(r"[a-f0-9]{12,}|\d{6,}", word)]
    return " ".join(words) if len(words) >= 5 else ""


def excerpt_matches(article, candidate):
    detail = candidate.detail or {}
    excerpt = detail.get("description") or plain_text(detail.get("feed_html", ""))
    words = title_tokens(excerpt)[:18]
    return (len(words) >= 8 and len(set(words)) >= 6
            and " ".join(words) in " ".join(title_tokens(plain_text(article.get("body_html", "")))[:600]))


def links_to_original(article, candidate):
    for value in article.get("original_urls", []):
        try:
            if canonical_url(value) == canonical_url(candidate.url):
                return True
        except ValueError:
            continue
    return False


def credits_original_publisher(article, candidate):
    """Recognize explicit wire credit, not a passing mention of a publisher."""
    domain = urlsplit(candidate.url).hostname.removeprefix("www.")
    brand = domain.split('.')[0]
    if brand in ("news", "feeds", "rss", "feed") or len(brand) < 3:
        return False
    author = " ".join(title_tokens(article.get("author", "")))
    opening = plain_text(article.get("body_html", "")).strip()[:150]
    return (author == brand or bool(re.match(r"\(?" + re.escape(brand) + r"\)?\s*(?:--|—|–)", opening, re.I)))


def written_article_url(url):
    path = urlsplit(url).path.lower()
    return not (re.search(r"/(?:videos?|podcasts?|audio|tv|radio|watch)(?:/|$)", path)
                or re.search(r"\.(?:mp4|mp3|m4a|m3u8|webm|ogg)$", path))


def checked_article_text(article, candidate, *, provider="Publication"):
    """Apply the same conservative readability checks used in the live audit."""
    text = plain_text(article.get("body_html", ""))
    title = article.get("title", "").strip()
    if not title or title.casefold() == "untitled":
        raise ValueError(provider + " returned an article without a usable title")
    if len(text.split()) < 200:
        raise ValueError(provider + " returned less than 200 words; the text needs review before import")
    alias = url_headline(candidate.url)
    alternate = (alias and titles_match(title, alias, strict=True)
                 and (excerpt_matches(article, candidate) or links_to_original(article, candidate)))
    if not titles_match(candidate.title, title) and not alternate:
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
    search = values.get("search_fallback", True)
    if not isinstance(title, str) or len(title) > 150:
        raise ValueError("Use a publication name of up to 150 characters")
    if not isinstance(preference, str) or len(preference) > 3000:
        raise ValueError("Keep reading preferences within 3,000 characters")
    if type(maximum) is not int or not 1 <= maximum <= 10:
        raise ValueError("Choose 1–10 articles per fetch")
    if type(fallback) is not bool:
        raise ValueError("Choose whether to try Freedium")
    if type(search) is not bool:
        raise ValueError("Choose whether to search for public copies")
    return {"title": title.strip() or urlsplit(address).hostname.removeprefix("www."), "url": address,
            "detail": {"preferences": preference.strip(), "max_articles": maximum,
                       "freedium_fallback": fallback, "search_fallback": search}}


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
        root = feed_root(payload)
        nodes = root.findall('./channel/item') + root.findall('{*}entry')
        descriptions, media, dates = {}, set(), {}
        for node in nodes[:500]:
            link = node.findtext('link') or next((link.get('href', '') for link in node.findall('{*}link')
                                               if link.get('rel', 'alternate') == 'alternate'), '')
            try:
                key = canonical_url(urljoin(address, link))
            except ValueError:
                continue
            descriptions[key] = plain_text(node.findtext('description') or node.findtext('{*}summary') or '')[:700]
            dates[key] = bool(iso_date(node.findtext('pubDate') or node.findtext('{*}published')
                                      or node.findtext('{*}updated') or node.findtext('{*}date') or '', ''))
            if any((enclosure.get('type', '').startswith(('audio/', 'video/')))
                   for enclosure in node.findall('enclosure')):
                media.add(key)
        result = []
        for item in entries:
            key = canonical_url(item.url)
            if same_publication(key, address) and written_article_url(key) and key not in media:
                detail = item.detail or {}
                result.append(replace(item, external_id=candidate_url(key), url=key, detail={**detail,
                    'description': descriptions.get(key) or plain_text(detail.get('feed_html', ''))[:700],
                    'date_known': dates.get(key, False)}))
        return result[:MAX_CANDIDATES]
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
        if record.get("@type") in ("VideoObject", "AudioObject", "PodcastEpisode"):
            continue
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
        if (not same_publication(url, address) or not written_article_url(url) or url in seen or url == canonical_url(address)
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
    try:
        doc = html.fromstring(payload)
    except (ValueError, html.etree.ParserError):
        raise ValueError("The page did not contain readable article HTML") from None
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
            "content_source": "subscriber" if authenticated else "publisher", "content_url": address,
            "original_urls": [urljoin(address, value) for value in doc.xpath(
                '//link[@rel="canonical"]/@href | //meta[@property="og:url"]/@content | //a/@href')][:300]}


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
                                     "freedium_fallback": source["detail"].get("freedium_fallback", True),
                                     "search_fallback": source["detail"].get("search_fallback", True)})
                for item in entries if (item.detail or {}).get("date_known") is False
                or not source.get("checked_at") or datetime.fromisoformat(item.published) >= since]

    def retrieval_steps(self, candidate):
        """Ordered providers; further reviewed retrievers can join this cascade."""
        steps = [("Feed", self._read_feed), ("Publisher", self._read_publisher),
                 ("Publication browser", self._read_browser)]
        if (candidate.detail or {}).get("freedium_fallback", True):
            steps.append(("Freedium", self._read_freedium))
        if (candidate.detail or {}).get("search_fallback", True):
            steps.append(("Public web search", self._read_public_copy))
        return steps

    def read_article(self, candidate, *, progress=None):
        if not written_article_url(candidate.url):
            raise ValueError("This is a video or audio entry, not a written article")
        errors = []
        self._text_reviews_left = MAX_TEXT_REVIEWS
        for name, read in self.retrieval_steps(candidate):
            if progress:
                progress("Trying " + name.lower() + " for " + candidate.title[:65] + "…")
            try:
                article = read(candidate)
                if article is not None:
                    # DDGS checks each usable copy inside its loop, so a rejected
                    # copy cannot prevent a later result from being examined.
                    if name != 'Public web search':
                        self._review_text(candidate, article)
                    if candidate.published and (candidate.detail or {}).get("date_known") is not False:
                        article = {**article, "published": candidate.published}
                    return article
            except (requests.RequestException, ValueError) as exc:
                # Keep one short reason per provider, instead of repeating login
                # instructions inside every article and again inside the toast.
                message = " ".join(str(exc).split())
                message = re.sub(r"\s*(?:Use|Try) Sign in / reconnect.*", "", message)
                errors.append(name + ": " + message[:180])
        raise ValueError("No readable copy found. " + "; ".join(errors))

    def _review_text(self, candidate, article, *, deadline=None):
        review = getattr(self, 'text_review', None)
        if not review:
            return
        remaining = getattr(self, '_text_reviews_left', MAX_TEXT_REVIEWS)
        if remaining <= 0:
            raise ValueError('The four-copy text-check limit was reached; try again later')
        self._text_reviews_left = remaining - 1
        timeout = min(105, deadline - time.monotonic()) if deadline is not None else 105
        if timeout <= 0:
            raise ValueError('The public-copy retrieval budget was reached')
        review(candidate, article, timeout=timeout)

    def _read_feed(self, candidate):
        detail = candidate.detail or {}
        # A full feed body can already contain all the subscriber's reading.
        body = detail.get("feed_html", "")
        if body and len(plain_text(body).split()) >= 200 and not feed_is_preview(body):
            return {"body_html": clean_body(body, candidate.url), "title": candidate.title,
                    "author": candidate.author, "url": candidate.url,
                    "content_source": "publisher", "content_url": candidate.url}

    def _read_publisher(self, candidate):
        from .feeds import page_is_paid
        page = PublicFeedHTTP().get(candidate.url)
        if page_is_paid(page.content):
            raise ValueError("Subscriber access is needed")
        return checked_article_text(extract_article(page.content, page.url or candidate.url, candidate.title,
                                                   candidate.author), candidate, provider="Publisher")

    def _read_browser(self, candidate):
        source_id = (candidate.detail or {}).get("publication_id")
        if not source_id:
            raise ValueError("No saved publication browser profile")
        page = self.browser.request(source_id, candidate.url)
        return checked_article_text(extract_article(page["html"].encode(), candidate.url, candidate.title,
                                                   candidate.author, authenticated=True), candidate, provider="Publication browser")

    def _read_freedium(self, candidate):
        return checked_article_text(fetch_freedium(candidate.url), candidate, provider="Freedium")

    def _read_public_copy(self, candidate):
        from .feeds import page_is_paid
        title = candidate.title.replace('"', ' ').strip()
        domain = urlsplit(candidate.url).hostname.removeprefix("www.")
        queries = [f'"{title}" -site:{domain}']
        alias = url_headline(candidate.url)
        if alias and not titles_match(title, alias, strict=True):
            queries.append(f'"{alias}" -site:{domain}')
        queries.append(f'{title} {candidate.author[:100]} -site:{domain}')
        queries = list(dict.fromkeys(queries))[:MAX_SEARCH_QUERIES]
        deadline = time.monotonic() + COPY_SEARCH_SECONDS
        visited = {canonical_url(candidate.url)}
        fetched, found, search_errors = 0, 0, []
        for query in queries:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or fetched >= MAX_COPY_PAGES:
                break
            try:
                records = PublicSearch().search(query, timeout=remaining)
            except ValueError as exc:
                search_errors.append(str(exc))
                continue
            for record in records:
                found += 1
                if fetched >= MAX_COPY_PAGES or time.monotonic() >= deadline:
                    break
                try:
                    address = canonical_url(record["url"])
                    if address in visited or not written_article_url(address):
                        continue
                    visited.add(address)
                    if not (titles_match(record["title"], candidate.title)
                            or alias and titles_match(record["title"], alias, strict=True)):
                        continue
                    fetched += 1
                    page = PublicFeedHTTP().get(address, timeout=7, deadline=deadline)
                    address = canonical_url(page.url or address)
                    if address == canonical_url(candidate.url) or page_is_paid(page.content):
                        continue
                    article = checked_article_text(extract_article(page.content, address), candidate,
                                                   provider="Public copy")
                    author_matches = bool(candidate.author and article.get("author")
                                          and titles_match(candidate.author, article["author"], strict=True))
                    date_matches = bool((candidate.detail or {}).get('date_known') is not False
                        and article.get("published") and candidate.published and
                        abs((datetime.fromisoformat(article["published"]) - datetime.fromisoformat(candidate.published)).total_seconds()) <= 172800)
                    wire_copy = (date_matches and credits_original_publisher(article, candidate)
                                 and titles_match(candidate.title, article['title'], strict=True))
                    if not (links_to_original(article, candidate) or excerpt_matches(article, candidate)
                            or author_matches and date_matches or wire_copy):
                        continue
                    result = {**article, "url": candidate.url, "content_source": "public_copy", "content_url": address}
                    self._review_text(candidate, result, deadline=deadline)
                    return result
                except (requests.RequestException, ValueError, TypeError):
                    continue
        if not found and search_errors:
            raise ValueError(search_errors[-1])
        raise ValueError(f"Searched public copies; none of {found} results could be verified as readable text for this article")


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
