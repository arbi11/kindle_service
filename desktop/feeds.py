"""Public, user-selected feed subscriptions. No publisher account is required."""
from __future__ import annotations

from datetime import datetime
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter

from connection_sdk import MAX_DOCUMENT_BYTES, MAX_FEED_BYTES, parse_feed
from .content import plain_text, sanitize
from .store import now


def public_url(value):
    """Accept public HTTPS addresses, without credentials or alternate ports."""
    value = str(value).strip()
    if len(value) > 2000 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("Enter a public website or feed address")
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        if (parsed.scheme != "https" or not host or parsed.port not in (None, 443)
                or parsed.username is not None or parsed.password is not None or "." not in host
                or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid"))):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError("Use a public HTTPS address without a login or custom port") from None
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


class PublicHTTPSAdapter(HTTPAdapter):
    """Pin each TLS connection to an already-checked public DNS address.

    Keeping the original Host, certificate hostname and TLS SNI also supports
    publisher custom domains. A second DNS lookup cannot redirect to the LAN.
    """

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        address = public_url(request.url)
        host = urlsplit(address).hostname
        try:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except socket.gaierror:
            raise ValueError("That website could not be found. Check the address and try again.") from None
        addresses = [entry[4][0] for entry in answers]
        if not addresses or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in addresses):
            raise ValueError("Connections can only read public websites, not local or private networks")
        # Prefer IPv4 on laptops without an IPv6 route.
        pinned = next((ip for ip in addresses if ":" not in ip), addresses[0])
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        host_params["host"] = pinned
        pool_kwargs.update(assert_hostname=host, server_hostname=host)
        request.headers["Host"] = host
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=pool_kwargs)


class PublicFeedHTTP:
    def get(self, url, timeout=25, *, limit=MAX_DOCUMENT_BYTES, allowed_hosts=None):
        with requests.Session() as session:
            session.trust_env = False
            session.headers["User-Agent"] = "Quib27/0.2 (personal feed reader)"
            session.mount("https://", PublicHTTPSAdapter())
            for _ in range(6):
                url = public_url(url)
                if allowed_hosts is not None and urlsplit(url).hostname not in allowed_hosts:
                    raise ValueError("This service redirected outside its allowed domains")
                response = session.get(url, timeout=(10, min(timeout, 30)), allow_redirects=False, stream=True)
                try:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get("Location", ""))
                        continue
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > limit:
                            raise ValueError("This website returned a document that is too large")
                        chunks.append(chunk)
                    response._content = b"".join(chunks)
                    response._content_consumed = True
                    response.encoding = response.encoding or "utf-8"
                    return response
                finally:
                    response.close()
        raise ValueError("This website redirected too many times")


def feed_root(payload):
    # Reuse the contract parser's bounded XML/declaration checks.
    parse_feed(payload, [], fallback_date=now())
    return ET.fromstring(payload)


def feed_info(payload, feed_url):
    root = feed_root(payload)
    atom = root.tag == "{http://www.w3.org/2005/Atom}feed"
    channel = root if atom else root.find("channel")
    if channel is None:
        raise ValueError("This RSS feed has no channel")
    title = channel.findtext("{*}title") or urlsplit(feed_url).hostname
    if atom:
        site = next((link.get("href") for link in channel.findall("{*}link")
                     if link.get("rel", "alternate") == "alternate"), "")
    else:
        site = channel.findtext("link") or ""
    site_url = urljoin(feed_url, site) if site else feed_url
    if urlsplit(site_url).scheme == "http":
        site_url = urlunsplit(urlsplit(site_url)._replace(scheme="https"))
    site_url = public_url(site_url)
    hosts = {urlsplit(feed_url).hostname, urlsplit(site_url).hostname}
    domains = sorted({host for domain in hosts for host in (domain, domain.removeprefix("www."), "www." + domain.removeprefix("www."))})
    entries = parse_feed(payload, domains, fallback_date=now(), include_content=True, default_author=title, base_url=feed_url)
    return title.strip()[:200], site_url, entries


def is_substack_page(page):
    # Custom domains use the same published Substack assets and metadata.
    from lxml import html
    doc = html.fromstring(page)
    return bool(doc.xpath('//meta[translate(@name,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="generator" and contains(translate(@content,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"substack")]'
                          ' | //script[contains(@src,"substackcdn.com/")]'
                          ' | //link[contains(@href,"substackcdn.com/")]'))


def resolve_subscription(value, kind):
    url = public_url(value)
    transport = PublicFeedHTTP()
    response = transport.get(url, limit=MAX_FEED_BYTES)
    feed_url = public_url(response.url or url)
    payload = response.content
    try:
        title, site, entries = feed_info(payload, feed_url)
        if kind == "substack":
            if not (urlsplit(site).hostname.endswith(".substack.com") or is_substack_page(transport.get(site).content)):
                raise ValueError("Use a Substack publication address, or add this feed under Websites & blogs")
    except (ValueError, ET.ParseError) as error:
        # Do not turn a well-formed but incompatible feed into a website guess.
        if payload.lstrip().startswith((b"<?xml", b"<rss", b"<feed")):
            raise error
        from lxml import html
        try:
            doc = html.fromstring(payload)
        except Exception:
            raise ValueError("This address did not return a readable website or feed") from None
        substack = urlsplit(feed_url).hostname.endswith(".substack.com") or is_substack_page(payload)
        if kind == "substack" and not substack:
            raise ValueError("Enter a Substack publication URL, including a publication’s own custom domain")
        links = doc.xpath('//link[contains(translate(@rel,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"alternate")'
                          ' and (@type="application/rss+xml" or @type="application/atom+xml")]/@href')
        candidates = [urljoin(feed_url, link) for link in links[:5]]
        # Common feeds are tried only at the website's root, not at article paths.
        if substack:
            candidates.insert(0, urljoin(feed_url, "/feed"))
        elif urlsplit(feed_url).path in ("", "/"):
            candidates += [urljoin(feed_url, path) for path in ("/feed/", "/feed", "/rss.xml", "/atom.xml")]
        found = False
        for candidate in dict.fromkeys(candidates):
            try:
                feed_response = transport.get(candidate, limit=MAX_FEED_BYTES)
                resolved = public_url(feed_response.url or candidate)
                title, site, entries = feed_info(feed_response.content, resolved)
                feed_url, found = resolved, True
                break
            except (ValueError, requests.RequestException, ET.ParseError):
                continue
        if not found:
            raise ValueError("No RSS or Atom feed was found. Paste the site's feed address instead.")
    return {"title": title, "url": feed_url, "detail": {"site_url": site}}


def paid_preview(body, *, substack=False):
    from lxml import html
    doc = html.fragment_fromstring(body or "", create_parent="div")
    text = plain_text(body).strip()
    return bool(doc.xpath('//*[contains(@class,"paywall") or contains(@class,"paid-preview")]') or
                re.search(r"(?:this (?:post|article) is for paid subscribers|subscribe to (?:read|continue reading))", text, re.IGNORECASE) or
                substack and re.search(r"\bread more\s*$", text, re.IGNORECASE))


def page_is_paid(payload):
    """Reject explicitly marked previews before they become 'full articles'."""
    from lxml import html
    doc = html.fromstring(payload)
    for value in doc.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            metadata = json.loads(value)
        except (ValueError, TypeError):
            continue
        pending = metadata if isinstance(metadata, list) else [metadata]
        for record in pending:
            if not isinstance(record, dict):
                continue
            if record.get("isAccessibleForFree") in (False, "false", "False"):
                return True
            pending.extend(record.get("@graph", []) if isinstance(record.get("@graph"), list) else [])
    return bool(doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," paywall ")]'))


class Subscription:
    def __init__(self, definition):
        self.definition = definition

    def http(self, session, source=None):
        return PublicFeedHTTP()

    def discover(self, session, source, since, *, token=""):
        response = self.http(session).get(source["url"], limit=MAX_FEED_BYTES)
        _, _, entries = feed_info(response.content, response.url or source["url"])
        entries.sort(key=lambda item: item.published, reverse=True)
        entries = [item for item in entries if not paid_preview((item.detail or {}).get("feed_html", ""), substack=self.definition["id"] == "substack")]
        # A quiet publication should still offer something on its first fetch.
        if not source.get("checked_at"):
            return entries[:3]
        return [item for item in entries if datetime.fromisoformat(item.published) >= since]

    def read_article(self, candidate):
        """Prefer publisher-supplied full content; extract pages for short feeds."""
        body = (candidate.detail or {}).get("feed_html", "")
        title, author = candidate.title, candidate.author
        text = plain_text(body).strip()
        if not body or len(text) < 80 or re.search(r"\bread more\s*$", text, re.IGNORECASE):
            import trafilatura
            response = self.http(None).get(candidate.url)
            if page_is_paid(response.content):
                raise ValueError("This post is a paid preview. Only public articles are collected.")
            body = trafilatura.extract(response.content, url=candidate.url, output_format="html",
                                      include_links=True, include_formatting=True, include_tables=True,
                                      include_comments=False) or ""
            metadata = trafilatura.extract_metadata(response.content, default_url=candidate.url)
            if metadata:
                title = title or metadata.title or ""
                author = metadata.author or author
        if not body or len(plain_text(body).strip()) < 80:
            raise ValueError("No readable article text was found. This post may contain only audio or video.")
        from lxml import html
        doc = html.fragment_fromstring(body, create_parent="div")
        for link in doc.iter("a"):
            link.set("href", urljoin(candidate.url, link.get("href", "")))
        body = sanitize(html.tostring(doc, encoding="unicode"))
        return {"body_html": body, "title": title, "author": author, "url": candidate.url}
