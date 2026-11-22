#!/usr/bin/env python3
"""Raindrop -> Kindle.

Collects new Raindrop.io bookmarks, turns them into one EPUB per batch and
emails it to a Kindle address. Runs entirely on this laptop: a systemd user
timer calls `tick` every few minutes and on login, and `tick` decides whether
a batch is due.

Batch rules:
  * Slots are fixed local times (default 08:00 M, 14:00 N, 20:00 E, India time).
  * Catch up once: missed slots collapse into one EPUB labelled with the most
    recent slot.
  * Bring forward: right after boot/wake, if the next slot is less than 30 min
    away it runs immediately and is skipped at its normal time.
  * No repeats: finished slots and sent bookmarks are recorded in the state file.
  * Empty batches produce no ebook.
  * Offline: nothing is marked done, so the next tick tries again.

The EPUB is named like Oct16_E_AgentHarness_BondYields.epub: date, slot letter
and a CamelCase keyword of up to 12 characters per article, in
table-of-contents order. The same name is the
EPUB's internal title, which is what the Kindle library shows.
"""

from __future__ import annotations

import argparse
from process_lock import acquire, release
import getpass
import io
import json
import logging
import os
import re
import smtplib
import socket
import ssl
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from email.message import EmailMessage
from html import escape, unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from ai_client import (
    DEFAULT_OPENAI_KEY_FILE, DEFAULT_OPENAI_MODEL, OPENAI_API,
    chat as ai_chat, decisions as ai_decisions, has_key as has_ai_key,
    openai_model, provider as ai_provider, usage_cost, total_cost, cost_label,
)

APP = "raindrop-kindle"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP
CONFIG_PATH = CONFIG_DIR / "config.toml"
STATE_PATH = STATE_DIR / "state.json"
BOOKS_DIR = STATE_DIR / "books"
LOCK_PATH = STATE_DIR / ".lock"

RAINDROP_API = "https://api.raindrop.io/rest/v1"
OPENROUTER_API = "https://openrouter.ai/api/v1"
OPENROUTER_DECISIONS = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_DECISION_MODEL = "~typesafe/jev-latest"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

# A tick that comes this long after the previous one means the laptop was
# off or asleep in between (the timer normally fires every 5 minutes).
WAKE_GAP = timedelta(minutes=12)
# Bookmarks saved this close to a fetch are looked at again next time, in case
# the Raindrop API had not caught up yet; `sent` keeps them from repeating.
WATERMARK_MARGIN = timedelta(minutes=15)
FAILURE_BACKOFF = timedelta(minutes=20)
ONLINE_WAIT = 90  # seconds to wait for the network after wake
MAX_EPUB_BYTES = 17 * 1024 * 1024  # Gmail caps messages at 25 MB after base64
IMAGE_BUDGET = 12 * 1024 * 1024
MAX_IMAGES_PER_ARTICLE = 12
ARTICLE_TYPES = {"link", "article"}

log = logging.getLogger(APP)


class Offline(Exception):
    """Network unavailable; retry on the next tick without backing off."""


class SetupError(Exception):
    """Configuration or credentials problem the user has to fix."""


# --------------------------------------------------------------------------
# Config and state
# --------------------------------------------------------------------------

DEFAULT_SLOTS = {"M": "08:00", "N": "14:00", "E": "20:00"}


def load_config(need_email: bool = True) -> dict:
    if not CONFIG_PATH.exists():
        raise SetupError(f"No config at {CONFIG_PATH}. Run `{APP} setup`.")
    with CONFIG_PATH.open("rb") as f:
        cfg = tomllib.load(f)
    missing = [k for k in ("raindrop_token", "kindle_emails") if not cfg.get(k)]
    smtp = cfg.get("smtp", {})
    if need_email:
        missing += [f"smtp.{k}" for k in ("host", "username", "password") if not smtp.get(k)]
    if missing:
        raise SetupError(f"Config is missing {', '.join(missing)}. Run `{APP} setup`.")
    if isinstance(cfg["kindle_emails"], str):
        cfg["kindle_emails"] = [cfg["kindle_emails"]]
    return cfg


def toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def write_config(cfg: dict) -> None:
    smtp = cfg["smtp"]
    sched = cfg.get("schedule", {})
    slots = sched.get("slots", DEFAULT_SLOTS)
    content = cfg.get("content", {})
    ai = cfg.get("ai", {})
    text = f"""# Raindrop -> Kindle settings. Edit freely; changes apply on the next tick.

# Raindrop test token: app.raindrop.io/settings/integrations -> Create new app -> Create test token
raindrop_token = {toml_str(cfg["raindrop_token"])}
# 0 = all bookmarks (except Trash), -1 = Unsorted, or a collection ID from the Raindrop URL
collection_id = {int(cfg.get("collection_id", 0))}

# Your Send-to-Kindle address(es), e.g. "name_abc@kindle.com"
kindle_emails = [{", ".join(toml_str(e) for e in cfg["kindle_emails"])}]

[smtp]
# Gmail: smtp.gmail.com, port 465, and an App Password (myaccount.google.com/apppasswords)
host = {toml_str(smtp["host"])}
port = {int(smtp.get("port", 465))}
username = {toml_str(smtp["username"])}
password = {toml_str(smtp.get("password", ""))}
# Must be on Amazon's "Approved Personal Document E-mail List"
sender = {toml_str(smtp.get("sender") or smtp["username"])}

[schedule]
timezone = {toml_str(sched.get("timezone", "Asia/Kolkata"))}
bring_forward_minutes = {int(sched.get("bring_forward_minutes", 30))}

[schedule.slots]
{chr(10).join(f"{k} = {toml_str(v)}" for k, v in slots.items())}

[content]
include_images = {"true" if content.get("include_images", True) else "false"}
# Use a bookmark's first Raindrop tag as its filename keyword when it has one
keyword_from_tags = {"true" if content.get("keyword_from_tags", True) else "false"}

[ai]
# Select one provider explicitly; OpenAI does not fall back to paid OpenRouter calls.
provider = {toml_str(ai.get("provider", "openrouter"))}
openai_api_key_file = {toml_str(ai.get("openai_api_key_file") or DEFAULT_OPENAI_KEY_FILE)}
openai_api_key = {toml_str(ai.get("openai_api_key", ""))}
openai_model = {toml_str(openai_model(cfg))}
openai_candidate_writer = {toml_str(ai.get("openai_candidate_writer", "built-in" if ai.get("candidate_writer", "built-in") == "built-in" else openai_model(cfg)))}
openai_decision_model = {toml_str(ai.get("openai_decision_model") or openai_model(cfg))}
# The built-in picker is used if the selected provider is unavailable.
openrouter_api_key = {toml_str(ai.get("openrouter_api_key", ""))}
decision_model = {toml_str(ai.get("decision_model", DEFAULT_DECISION_MODEL))}
# Who writes the candidates: "built-in" (free, on this laptop) or an OpenRouter chat
# model such as "z-ai/glm-5.3-flash" (~$0.0002 per batch, more varied ideas).
candidate_writer = {toml_str(ai.get("candidate_writer", "built-in"))}
"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, CONFIG_PATH)


class Schedule:
    def __init__(self, cfg: dict):
        sched = cfg.get("schedule", {})
        self.tz = ZoneInfo(sched.get("timezone", "Asia/Kolkata"))
        slots = sched.get("slots", DEFAULT_SLOTS)
        self.slots = sorted((dtime.fromisoformat(v), k) for k, v in slots.items())
        self.bring_forward = timedelta(minutes=sched.get("bring_forward_minutes", 30))

    def _around(self, now: datetime):
        day = now.astimezone(self.tz).date()
        for offset in (-1, 0, 1):
            d = day + timedelta(days=offset)
            for t, _ in self.slots:
                yield datetime.combine(d, t, self.tz)

    def due(self, now: datetime) -> datetime:
        """The most recent slot at or before `now`."""
        return max(s for s in self._around(now) if s <= now)

    def next(self, now: datetime) -> datetime:
        return min(s for s in self._around(now) if s > now)

    def label(self, slot: datetime) -> str:
        slot = slot.astimezone(self.tz)
        letter = next((k for t, k in self.slots if t == slot.time()), "X")
        return f"{slot:%b%d}_{letter}"


def now_local(sched: Schedule) -> datetime:
    return datetime.now(sched.tz).replace(microsecond=0)


def parse_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def load_state(sched: Schedule, now: datetime) -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    # First run: the next batch covers everything saved since the last slot.
    due = sched.due(now).isoformat()
    return {
        "watermark": due,
        "last_slot_done": due,
        "last_tick": None,
        "pending_open": False,
        "retry_after": None,
        "sent": {},
        "history": [],
    }


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_PATH)


class Lock:
    def __init__(self, wait: bool):
        self.wait = wait

    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.f = LOCK_PATH.open("a+")
        try:
            acquire(self.f, blocking=self.wait)
        except BlockingIOError:
            self.f.close()
            return False
        return True

    def __exit__(self, *exc):
        if not self.f.closed:
            release(self.f)
            self.f.close()


# --------------------------------------------------------------------------
# Network helpers
# --------------------------------------------------------------------------

def http():
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def is_offline_error(exc: BaseException) -> bool:
    import requests

    return isinstance(
        exc, (requests.ConnectionError, requests.Timeout, socket.gaierror, socket.timeout, ConnectionError)
    )


def wait_for_network(timeout: int = ONLINE_WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            socket.create_connection(("api.raindrop.io", 443), timeout=5).close()
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(10)


# --------------------------------------------------------------------------
# Raindrop
# --------------------------------------------------------------------------

def raindrop_get(session, cfg: dict, path: str, **params) -> dict:
    try:
        r = session.get(
            f"{RAINDROP_API}{path}",
            params=params,
            headers={"Authorization": f"Bearer {cfg['raindrop_token']}"},
            timeout=30,
        )
    except Exception as e:
        if is_offline_error(e):
            raise Offline(f"Raindrop unreachable: {e}") from e
        raise
    if r.status_code == 401:
        raise SetupError("Raindrop rejected the token. Run `raindrop-kindle setup` with a new test token.")
    if r.status_code == 429 or r.status_code >= 500:
        raise Offline(f"Raindrop returned HTTP {r.status_code}")
    r.raise_for_status()
    return r.json()


def fetch_new_bookmarks(session, cfg: dict, state: dict, since: datetime | None = None) -> list[dict]:
    """Bookmarks created after the watermark that haven't been sent, oldest first."""
    if since is None:
        since = parse_dt(state["watermark"]) - WATERMARK_MARGIN
    sent = state["sent"]
    cid = int(cfg.get("collection_id", 0))
    found, page = [], 0
    while True:
        data = raindrop_get(session, cfg, f"/raindrops/{cid}", sort="-created", perpage=50, page=page)
        items = data.get("items", [])
        done = len(items) < 50
        for it in items:
            created = parse_dt(it["created"])
            if created <= since:
                done = True
                break
            if str(it["_id"]) in sent:
                continue
            if it.get("type", "link") not in ARTICLE_TYPES:
                log.info("Skipping %s bookmark: %s", it.get("type"), it.get("link"))
                continue
            found.append(it)
        if done:
            break
        page += 1
    found.sort(key=lambda it: it["created"])
    return found


# --------------------------------------------------------------------------
# Articles
# --------------------------------------------------------------------------

@dataclass
class Article:
    index: int
    bookmark: dict
    title: str
    url: str
    body_html: str | None = None
    author: str | None = None
    published: str | None = None
    site: str | None = None
    images: list[tuple[str, bytes]] = field(default_factory=list)
    offline: bool = False
    keyword: str = ""
    candidates: list[str] = field(default_factory=list)


class ImageBudget:
    def __init__(self, limit: int):
        self.left = limit
        self.lock = threading.Lock()

    def take(self, n: int) -> bool:
        with self.lock:
            if n > self.left:
                return False
            self.left -= n
            return True


DROP_TAGS = {"script", "style", "iframe", "form", "input", "button", "noscript", "svg", "video",
             "audio", "object", "embed", "canvas", "select", "textarea", "picture", "source"}
KEEP_ATTRS = {"href", "src", "alt", "colspan", "rowspan"}


def fetch_article(session, art: Article, include_images: bool, budget: ImageBudget) -> Article:
    import trafilatura

    import requests

    try:
        try:
            r = session.get(art.url, timeout=25)
        except requests.Timeout:
            r = session.get(art.url, timeout=45)  # slow mirrors often answer the second time
        r.raise_for_status()
        raw = r.content
        body = trafilatura.extract(
            raw, url=art.url, output_format="html", include_images=include_images,
            include_links=True, include_formatting=True, include_tables=True,
            include_comments=False, favor_recall=True,
        )
        meta = trafilatura.extract_metadata(raw, default_url=art.url)
        if meta:
            art.author, art.published, art.site = meta.author, meta.date, meta.sitename
            if not art.title and meta.title:
                art.title = meta.title
        if body:
            try:
                art.body_html = clean_body(session, art, body, include_images, budget)
            except Exception as e:
                log.warning("Couldn't tidy %s (%s); using the plain extract", art.url, e)
                art.images.clear()
                art.body_html = re.sub(r"<img\b[^>]*>", "", body)
    except Exception as e:
        art.offline = is_offline_error(e)
        log.warning("Couldn't fetch %s: %s", art.url, e)
    return art


def clean_body(session, art: Article, body: str, include_images: bool, budget: ImageBudget) -> str:
    import lxml.html

    root = lxml.html.fromstring(body)
    container = root.find("body") if root.tag == "html" else root
    if container is None:
        container = root
    for el in list(container.iter()):
        if not isinstance(el.tag, str):
            continue
        if el.tag in DROP_TAGS:
            el.drop_tree()
            continue
        for attr in list(el.attrib):
            if attr not in KEEP_ATTRS:
                del el.attrib[attr]
        if el.tag == "a":
            href = urljoin(art.url, el.get("href", ""))
            if urlparse(href).scheme in ("http", "https", "mailto") and not href.startswith(art.url + "#"):
                el.set("href", href)
            else:
                el.attrib.pop("href", None)
    for n, img in enumerate(list(container.iter("img"))):
        name = None
        if include_images and n < MAX_IMAGES_PER_ARTICLE:
            name = embed_image(session, art, img.get("src", ""), n, budget)
        if name:
            img.set("src", name)
        else:
            img.drop_tree()
    # Old-school sites lay pages out with tables: drop empty ones, unwrap text ones.
    for table in list(container.iter("table")):
        if table.getparent() is None:
            continue  # already removed with an outer table
        text = table.text_content().strip()
        if not text and table.find(".//img") is None:
            table.drop_tree()
        elif len(table.findall(".//tr")) <= 2 and len(text) > 1000:
            for el in list(table.iter("table", "tbody", "thead", "tr", "td", "th")):
                el.drop_tag()
    # Drop a leading heading that just repeats the title.
    first = next((c for c in container if isinstance(c.tag, str)), None)
    if first is not None and first.tag in ("h1", "h2") and norm(first.text_content()) == norm(art.title):
        first.drop_tree()
    parts = [escape(container.text or "")]
    parts += [lxml.html.tostring(c, encoding="unicode") for c in container]
    return "".join(parts)


def embed_image(session, art: Article, src: str, n: int, budget: ImageBudget) -> str | None:
    from PIL import Image

    url = urljoin(art.url, src)
    if urlparse(url).scheme not in ("http", "https"):
        return None
    try:
        r = session.get(url, timeout=15, stream=True, headers={"Referer": art.url})
        r.raise_for_status()
        data = r.raw.read(6 * 1024 * 1024 + 1, decode_content=True)
        if len(data) > 6 * 1024 * 1024:
            return None
        im = Image.open(io.BytesIO(data))
        im.seek(0)
        if im.width < 80 or im.height < 80:
            return None  # icons, tracking pixels
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((1000, 1400))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72, optimize=True, progressive=False)
        out = buf.getvalue()
    except Exception as e:
        log.debug("Skipping image %s: %s", url, e)
        return None
    if not budget.take(len(out)):
        return None
    name = f"images/a{art.index:02d}_{n:02d}.jpg"
    art.images.append((name, out))
    return name


def norm(s: str | None) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


# --------------------------------------------------------------------------
# Keywords
# --------------------------------------------------------------------------

STOPWORDS = set("""
a an the and or but nor for so yet of in on at to from by with without into onto over under about
above below after before between through during against among around across via vs versus per
is are was were be been being am do does did done have has had having can could will would shall
should may might must i me my we our you your he him his she her it its they them their this that
these those there here what which who whom whose why how when where not no yes all any both each
few more most other some such only own same than too very just also even ever still again much many
new now guide best top ways way things thing really actually finally simply lot lots one two three
says said say tells told asks asked gets got get make makes made making take takes took go goes going
went come comes came look looks need needs want wants know knows think thinks use uses used using
announces announced launches launched unveils unveiled reveals revealed introduces introduced
releases released explains explained shows showed shown finds found raises raised warns warned
plans planned adds added becomes become became gives given keeps kept let lets help helps
why's what's here's that's it's isn't aren't don't doesn't can't won't
part chapter episode issue edition update read review analysis opinion explained introduction
big small good bad high low long short first last next latest old young real free full hard
easy better worse inside behind steady rising falling holds hold rise fall amid ahead despite
""".split())


KEYWORD_MIN, KEYWORD_MAX = 12, 20  # characters per article in the filename
FILL_MAX_ZIPF = 5.2  # words added after the first must be at least this specific


def ranked_words(title: str, url: str) -> list[tuple[float, int, str]]:
    """Title words worth putting in a filename, best first, as (score, position, Word)."""
    from wordfreq import zipf_frequency

    # "Headline | Site Name" -> "Headline"
    parts = re.split(r"\s+[|•–—-]\s+", title)
    main = max(parts, key=len) if parts else title
    main = re.sub(r"\b[A-Za-z]+n['’]t\b", " ", main)  # isn't, didn't
    main = re.sub(r"['’](s|re|ve|ll|d|m)\b", "", main)  # IBM's -> IBM
    matches = list(re.finditer(r"[A-Za-z][A-Za-z0-9]*", main))
    tokens = [m.group() for m in matches]
    domain_words = set(re.findall(r"[a-z]+", urlparse(url).netloc.lower())) - {"www", "com", "org", "net", "co", "in", "io"}
    letters = [c for c in main if c.isalpha()]
    shouty = letters and sum(c.isupper() for c in letters) / len(letters) > 0.6
    later = tokens[1:]
    sentence_case = sum(t[0].isupper() for t in later) <= len(later) / 2

    # In sentence-case headlines, keep multi-word names together: "San Francisco".
    groups: list[list[int]] = []
    for k, tok in enumerate(tokens):
        prev = tokens[k - 1] if k else ""
        if (sentence_case and groups and groups[-1][-1] == k - 1 and tok[0].isupper() and prev[0].isupper()
                and main[matches[k - 1].end():matches[k].start()] == " "
                and prev.lower() not in STOPWORDS and tok.lower() not in STOPWORDS):
            groups[-1].append(k)
        else:
            groups.append([k])

    scored, plain, seen = [], [], set()
    for group in groups:
        i, toks = group[0], [tokens[k] for k in group]
        low = "".join(toks).lower()
        if len(toks) == 1 and (low in STOPWORDS or low in domain_words) or low in seen:
            continue
        seen.add(low)
        tok = toks[0]
        acronym = len(toks) == 1 and tok.isupper() and 2 <= len(tok) <= 6 and not shouty
        if len(toks) == 1 and len(tok) < 3 and not acronym:
            continue
        score = min(zipf_frequency(t.lower(), "en") for t in toks)
        if acronym:
            score -= 3  # AI, MCP, IBM: specific, but "AI" shouldn't crowd out everything
        elif sentence_case and (i > 0 or len(toks) > 1) and tok[0].isupper():
            score -= 1.5  # proper noun in a sentence-case headline
        if not acronym and len(tok) > 5 and low.endswith(("ly", "est")):
            score += 2  # "Terribly", "Coolest" say little about the topic
        word = "".join(t if t.isupper() or (any(c.isupper() for c in t[1:]) and not shouty)
                       else t.lower().capitalize() for t in toks)  # AI, OpenAI, iPhone, SanFrancisco
        if score > 5.8:
            plain.append((FILL_MAX_ZIPF, i, word))  # too common on its own ("Work")
            continue
        scored.append((score, i, word))
    scored.sort()
    return scored or plain[:2]  # "How to Do Great Work" -> Great, Work


def compose_keyword(ranked: list[tuple[float, int, str]], lead: list[str] = (),
                    max_len: int = KEYWORD_MAX, max_words: int = 3) -> str:
    """Best words that fit in max_len characters, CamelCase in title order: BondYields."""
    chosen = [(k - len(lead), w) for k, w in enumerate(lead)]  # tag words go first
    taken = {w.lower() for w in lead}
    total = sum(len(w) for w in lead)
    for score, i, w in ranked:
        if len(chosen) >= max_words:
            break
        if w.lower() in taken or (chosen and score > FILL_MAX_ZIPF):
            continue
        if total + len(w) <= max_len:
            chosen.append((i, w))
            taken.add(w.lower())
            total += len(w)
    if not chosen:
        return ranked[0][2][:max_len] if ranked else ""
    return "".join(w for _, w in sorted(chosen))


def tag_words(tags: list[str], max_len: int = KEYWORD_MAX) -> list[str]:
    for tag in tags or []:
        words = [w if w.isupper() else w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", tag)]
        if words:
            joined = "".join(words)
            return words if len(joined) <= max_len else [joined[:max_len]]  # MachineLearn
    return []


def keyword_candidates(art: Article, from_tags: bool, n: int = 5) -> list[str]:
    """Up to n distinct filename keywords for one article, best guess first."""
    ranked = ranked_words(art.title, art.url)
    lead = tag_words(art.bookmark.get("tags", [])) if from_tags else []
    options = [compose_keyword(ranked, lead)] if lead else []
    options += [compose_keyword(ranked), compose_keyword(ranked, max_words=2)]
    options += [compose_keyword(ranked[k:]) for k in range(1, 4)]
    options += [w for score, _, w in ranked if len(w) <= KEYWORD_MAX and score <= FILL_MAX_ZIPF]
    weak = {w for score, _, w in ranked if score > FILL_MAX_ZIPF}  # "Doing", "End" on their own
    options = list(dict.fromkeys(o for o in options if o and o not in weak)) or [compose_keyword(ranked)]
    # Within the 12-20 character range first, keeping the original order otherwise.
    return sorted(options, key=lambda o: not KEYWORD_MIN <= len(o) <= KEYWORD_MAX)[:n]


def clean_keyword(s: str | None) -> str | None:
    kw = re.sub(r"[^A-Za-z0-9]", "", s or "")
    return kw if 4 <= len(kw) <= KEYWORD_MAX else None


WRITER_PROMPT = f"""You suggest filename keywords for the articles in an ebook.
For each article, write 5 different keywords that tell a reader what it is about at a glance.

Rules:
- CamelCase, letters and digits only, {KEYWORD_MIN} to {KEYWORD_MAX} characters.
- Keep acronyms and product names as written (AI, MCP, DeepSeek, JSONL).
- Name the subject, not the genre: BondYieldsRising, not FinanceNews.

Reply with JSON only: {{"articles": [{{"id": <article id>, "keywords": ["...", ...]}}]}}."""


def snippet(art: Article, chars: int = 300) -> str:
    text = re.sub(r"<[^>]+>", " ", art.body_html or "") or art.bookmark.get("excerpt", "")
    return re.sub(r"\s+", " ", unescape(text)).strip()[:chars]


def article_state(articles: list[Article]) -> list[dict]:
    return [{"id": a.index, "title": a.title, "site": urlparse(a.url).netloc.removeprefix("www."),
             "opening": snippet(a)} for a in articles]


def ai_candidates(session, cfg: dict, articles: list[Article]) -> dict[int, list[str]]:
    """Optionally have a chat model write keyword ideas. Empty dict when off or failing."""
    ai = cfg.get("ai", {})
    writer = ai.get("candidate_writer", "built-in")
    if ai_provider(cfg) == "openai":
        writer = ai.get("openai_candidate_writer", "built-in" if writer == "built-in" else openai_model(cfg))
    if writer == "built-in" or not has_ai_key(cfg):
        return {}
    schema = {"type": "object", "additionalProperties": False, "required": ["articles"], "properties": {
        "articles": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "keywords"],
            "properties": {"id": {"type": "integer"}, "keywords": {"type": "array", "items": {"type": "string"}}}}}}}
    try:
        data = ai_chat(session, cfg, {
            "model": writer,
            "messages": [{"role": "system", "content": WRITER_PROMPT},
                         {"role": "user", "content": json.dumps({"articles": article_state(articles)}, ensure_ascii=False)}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "ideas", "strict": True, "schema": schema}},
            "reasoning": {"effort": ai.get("reasoning_effort", "minimal")},
            "temperature": 0.4,
            "max_tokens": 1500 + 80 * len(articles),
            "usage": {"include": True},
        })
        content = re.sub(r"^```(?:json)?|```$", "", (data["choices"][0]["message"]["content"] or "").strip()).strip()
        ideas = {int(a["id"]): a["keywords"] for a in json.loads(content)["articles"]}
    except Exception as e:
        log.warning("Candidate writer %s unavailable (%s); using built-in candidates", writer, e)
        return {}
    usage = data.get("usage") or {}
    log.info("Candidates written by %s (%s tokens%s)", data.get("model", writer), usage.get("total_tokens", "?"),
             f", ${usage['cost']:.5f}" if isinstance(usage.get("cost"), (int, float)) else "")
    return ideas


def jev_decide(session, cfg: dict, articles: list[Article]) -> dict[int, list[str]]:
    """Choose article keywords with the selected provider; empty on failure."""
    ai = cfg.get("ai", {})
    if not has_ai_key(cfg):
        return {}
    questions = {
        f"a{a.index}": {
            "type": "choice",
            "instructions": (f"Article {a.index} is titled {a.title!r}. Which filename keyword best tells a "
                             "reader what this article is about? Prefer specific subjects over generic words."),
            "criteria": {c: None for c in a.candidates},
        }
        for a in articles if len(a.candidates) > 1
    }
    if not questions:
        return {}
    model = ((ai.get("openai_decision_model") or openai_model(cfg)) if ai_provider(cfg) == "openai"
             else ai.get("decision_model") or DEFAULT_DECISION_MODEL)
    try:
        data = ai_decisions(session, cfg, {"articles": article_state(articles)}, questions)
        ranked = {}
        for name, ans in data["answers"].items():
            probs = ans.get("probabilities") or {}
            ranked[int(name[1:])] = [ans["choice"]] + sorted((o for o in probs if o != ans["choice"]),
                                                             key=probs.get, reverse=True)
    except Exception as e:
        log.warning("%s keyword selection unavailable (%s); using built-in keywords", ai_provider(cfg), e)
        return {}
    usage = data.get("usage") or {}
    log.info("%s chose keywords (%s input tokens%s)", data.get("model", model), usage.get("input_tokens", usage.get("prompt_tokens", "?")),
             f", ${usage['cost']:.6f}" if isinstance(usage.get("cost"), (int, float)) else "")
    return ranked


def assign_keywords(articles: list[Article], from_tags: bool, session=None, cfg: dict | None = None) -> str:
    """Set art.keyword for each article; return the selected picker."""
    for art in articles:
        art.candidates = keyword_candidates(art, from_tags)
    ranked: dict[int, list[str]] = {}
    if session is not None and cfg:
        ideas = ai_candidates(session, cfg, articles)
        for art in articles:
            written = [k for k in map(clean_keyword, ideas.get(art.index, [])) if k]
            art.candidates = list(dict.fromkeys(written[:5] + art.candidates))[:8]
        ranked = jev_decide(session, cfg, articles)
    used: set[str] = set()
    for art in articles:
        host = urlparse(art.url).netloc.removeprefix("www.").split(".")[0]
        options = [k for k in map(clean_keyword, ranked.get(art.index, [])) if k] + art.candidates
        options.append(re.sub(r"[^A-Za-z0-9]", "", host.capitalize())[:KEYWORD_MAX] or "Article")
        kw = next((o for o in options if o.lower() not in used), None)
        if kw is None:
            base, n = options[0][:KEYWORD_MAX - 2], 2
            while f"{base}{n}".lower() in used:
                n += 1
            kw = f"{base}{n}"
        used.add(kw.lower())
        art.keyword = kw
    return ("OpenAI" if ai_provider(cfg) == "openai" else "Jev") if ranked else "built-in"


def book_name(label: str, articles: list[Article], max_len: int = 200) -> str:
    name = label
    for i, art in enumerate(articles):
        rest = len(articles) - i
        candidate = f"{name}_{art.keyword}"
        if len(candidate) > max_len and rest > 0:
            return f"{name}_and{rest}more"
        name = candidate
    return name


# --------------------------------------------------------------------------
# EPUB
# --------------------------------------------------------------------------

CSS = """
body { font-family: serif; line-height: 1.45; }
h1 { font-size: 1.5em; line-height: 1.2; margin: 0 0 .4em; }
p.meta { color: #555; font-size: .85em; margin: 0 0 .2em; }
p.source { font-size: .8em; margin: 0 0 1.2em; word-break: break-all; }
blockquote.note { border-left: 3px solid #999; margin: 0 0 1.2em; padding-left: .8em; font-style: italic; }
img { max-width: 100%; height: auto; }
pre, code { font-family: monospace; font-size: .85em; white-space: pre-wrap; }
table { border-collapse: collapse; } td, th { border: 1px solid #aaa; padding: .2em .4em; }
p.end { margin-top: 2em; font-size: .8em; color: #555; text-align: center; }
"""


def chapter_html(art: Article, include_images: bool) -> str:
    bm = art.bookmark
    host = urlparse(art.url).netloc.removeprefix("www.")
    bits = [art.site or host]
    if art.author:
        bits.append(art.author)
    if art.published:
        bits.append(art.published)
    out = [
        f"<h1>{escape(art.title)}</h1>",
        f'<p class="meta">{escape(" · ".join(bits))}</p>',
        f'<p class="source"><a href="{escape(art.url)}">{escape(art.url)}</a></p>',
    ]
    if bm.get("note"):
        out.append(f'<blockquote class="note"><p>{escape(bm["note"])}</p></blockquote>')
    if art.body_html:
        body = art.body_html
        if not include_images:
            body = re.sub(r"<img\b[^>]*>", "", body)
        out.append(body)
    else:
        if bm.get("excerpt"):
            out.append(f"<p>{escape(bm['excerpt'])}</p>")
        out.append("<p><em>The full text couldn't be extracted. Open the link above to read it.</em></p>")
    out.append('<p class="end">· · ·</p>')
    return "<div>" + "".join(out) + "</div>"


def build_epub(name: str, articles: list[Article], out_dir: Path, slot: datetime, include_images: bool) -> Path:
    from epub_writer import write_epub
    chapters = [(art.title, chapter_html(art, include_images)) for art in articles]
    images = [image for art in articles for image in art.images] if include_images else []
    return write_epub(out_dir / f"{name}.epub", name, chapters, author="Raindrop", css=CSS,
                      identifier=f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, APP + ':' + name + ':' + slot.isoformat())}",
                      images=images)


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

def send_to_kindle(cfg: dict, path: Path, subject: str | None = None,
                   text: str | None = None, html: str | None = None) -> None:
    """Email the EPUB. The text/HTML body is for your Sent folder (searchable in Gmail);
    Send-to-Kindle only delivers the attachment."""
    smtp = cfg["smtp"]
    msg = EmailMessage()
    msg["From"] = smtp.get("sender") or smtp["username"]
    msg["To"] = ", ".join(cfg["kindle_emails"])
    msg["Subject"] = subject or path.stem
    msg.set_content(text or f"{path.stem}\n\nSent by raindrop-kindle.\n")
    if html:
        msg.add_alternative(html, subtype="html")
    msg.add_attachment(path.read_bytes(), maintype="application", subtype="epub+zip", filename=path.name)

    host, port = smtp["host"], int(smtp.get("port", 465))
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=90)
        else:
            server = smtplib.SMTP(host, port, timeout=90)
            server.starttls(context=ctx)
        with server:
            server.login(smtp["username"], smtp["password"])
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise SetupError(f"SMTP login failed for {smtp['username']}: {e.smtp_error.decode(errors='replace')}") from e
    except smtplib.SMTPException:
        raise
    except OSError as e:
        raise Offline(f"Mail server unreachable: {e}") from e


def notify(summary: str, body: str = "", urgent: bool = False) -> None:
    try:
        subprocess.run(
            ["notify-send", "-a", "Raindrop → Kindle", "-i", "ebook-reader",
             "-u", "critical" if urgent else "normal", summary, body],
            timeout=10, check=False, capture_output=True,
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# Batches
# --------------------------------------------------------------------------

def email_body(name: str, articles: list[Article]) -> tuple[str, str]:
    """Plain-text and HTML email bodies listing the book's articles, so Gmail can search them."""
    text = [name, f"{len(articles)} article(s) from Raindrop", ""]
    html = [f"<h2>{escape(name)}</h2><p>{len(articles)} article(s) from Raindrop</p>"]
    for n, a in enumerate(articles, 1):
        site = a.site or urlparse(a.url).netloc.removeprefix("www.")
        body = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", a.body_html or ""))).strip()
        opening = " ".join((body or a.bookmark.get("excerpt", "")).split()[:80])
        note = a.bookmark.get("note", "")
        text += [f"{n}. {a.title}", f"   {site} · {a.url}"] + ([f"   Note: {note}"] if note else []) \
            + ([f"   {opening}..."] if opening else []) + [""]
        html.append(f'<h3>{n}. <a href="{escape(a.url)}">{escape(a.title)}</a></h3><p><i>{escape(site)}</i></p>'
                    + (f"<p><b>Note:</b> {escape(note)}</p>" if note else "")
                    + (f"<p>{escape(opening)}…</p>" if opening else ""))
    text.append("Sent by raindrop-kindle")
    html.append('<p style="color:#888">Sent by raindrop-kindle</p>')
    return "\n".join(text), "".join(html)


def make_book(session, cfg: dict, bookmarks: list[dict], label: str, slot: datetime, out_dir: Path) -> tuple[Path, list[Article]]:
    content = cfg.get("content", {})
    include_images = content.get("include_images", True)
    articles = [
        Article(index=i + 1, bookmark=bm, title=(bm.get("title") or "").strip(), url=bm["link"])
        for i, bm in enumerate(bookmarks)
    ]
    budget = ImageBudget(IMAGE_BUDGET)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda a: fetch_article(session, a, include_images, budget), articles))
    if all(a.offline for a in articles):
        raise Offline("Couldn't reach any article")
    for a in articles:
        a.title = a.title or a.url
    chooser = assign_keywords(articles, content.get("keyword_from_tags", True), session, cfg)
    log.info("Keywords chosen by %s", chooser)
    name = book_name(label, articles)
    path = build_epub(name, articles, out_dir, slot, include_images)
    if path.stat().st_size > MAX_EPUB_BYTES and include_images:
        log.info("%s is %.1f MB; rebuilding without images", path.name, path.stat().st_size / 1e6)
        path = build_epub(name, articles, out_dir, slot, include_images=False)
    return path, articles


def run_batch(cfg: dict, state: dict, slot: datetime, sched: Schedule, mark_slot: bool) -> None:
    label = sched.label(slot)
    session = http()
    started = now_local(sched)
    bookmarks = fetch_new_bookmarks(session, cfg, state)
    if not bookmarks:
        log.info("%s: no new bookmarks, nothing to send", label)
    else:
        log.info("%s: building ebook from %d bookmark(s)", label, len(bookmarks))
        path, articles = make_book(session, cfg, bookmarks, label, slot, BOOKS_DIR)
        log.info("Built %s (%.1f MB); emailing %s", path.name, path.stat().st_size / 1e6, ", ".join(cfg["kindle_emails"]))
        text, html = email_body(path.stem, articles)
        send_to_kindle(cfg, path, subject=f"Raindrop: {path.stem}", text=text, html=html)
        for bm in bookmarks:
            state["sent"][str(bm["_id"])] = bm["created"]
        state["history"] = (state["history"] + [{
            "slot": slot.isoformat(), "file": path.name, "articles": len(articles),
            "sent_at": now_local(sched).isoformat(),
        }])[-50:]
        log.info("Sent %s with %d article(s)", path.name, len(articles))
        notify(f"Sent to Kindle: {path.stem}", f"{len(articles)} article(s)")

    watermark = max(parse_dt(state["watermark"]), started - WATERMARK_MARGIN)
    state["watermark"] = watermark.isoformat()
    cutoff = watermark - timedelta(days=2)
    state["sent"] = {k: v for k, v in state["sent"].items() if parse_dt(v) >= cutoff}
    if mark_slot:
        state["last_slot_done"] = max(parse_dt(state["last_slot_done"]), slot).isoformat()
        state["pending_open"] = False
    state["retry_after"] = None
    save_state(state)


def choose_slot(state: dict, sched: Schedule, now: datetime) -> datetime | None:
    done = parse_dt(state["last_slot_done"])
    due, upcoming = sched.due(now), sched.next(now)
    if state["pending_open"] and upcoming - now < sched.bring_forward and upcoming > done:
        return upcoming  # just opened the laptop and the next slot is close: run it now
    if due > done:
        return due  # one or more slots missed or just reached: one book, latest label
    return None


def cmd_tick(args) -> int:
    with Lock(wait=False) as locked:
        if not locked:
            return 0
        try:
            cfg = load_config()
        except SetupError as e:
            log.debug("%s", e)
            return 0
        sched = Schedule(cfg)
        now = now_local(sched)
        state = load_state(sched, now)

        last = parse_dt(state["last_tick"])
        if last is None or now - last > WAKE_GAP:
            state["pending_open"] = True
            log.info("Boot/wake detected (last check %s)", last.strftime("%b %d %H:%M") if last else "never")
        state["last_tick"] = now.isoformat()
        save_state(state)

        retry = parse_dt(state.get("retry_after"))
        if retry and now < retry:
            return 0
        slot = choose_slot(state, sched, now)
        if slot is None:
            if state["pending_open"]:
                state["pending_open"] = False
                save_state(state)
            return 0
        if slot > now:
            log.info("Next slot %s is under %d min away; running it now",
                     sched.label(slot), sched.bring_forward.seconds // 60)
        elif now - slot > timedelta(minutes=10):
            log.info("Catching up as %s (missed slots are merged into one book)", sched.label(slot))

        if not wait_for_network():
            log.info("Offline; will retry on the next check")
            return 0
        try:
            run_batch(cfg, state, slot, sched, mark_slot=True)
        except Offline as e:
            log.info("Offline (%s); will retry on the next check", e)
        except Exception as e:
            log.error("Batch %s failed: %s", sched.label(slot), e, exc_info=not isinstance(e, SetupError))
            state["retry_after"] = (now + FAILURE_BACKOFF).isoformat()
            save_state(state)
            notify("Raindrop → Kindle failed", str(e), urgent=True)
            return 1
    return 0


def cmd_send_now(args) -> int:
    cfg = load_config()
    sched = Schedule(cfg)
    with Lock(wait=True):
        now = now_local(sched)
        state = load_state(sched, now)
        try:
            run_batch(cfg, state, sched.due(now), sched, mark_slot=False)
        except Offline as e:
            print(f"Offline: {e}", file=sys.stderr)
            return 1
    return 0


def cmd_preview(args) -> int:
    cfg = load_config(need_email=False)
    sched = Schedule(cfg)
    now = now_local(sched)
    state = load_state(sched, now)
    since = now - timedelta(hours=args.hours) if args.hours else None
    if since:
        state = {**state, "sent": {}}
    session = http()
    bookmarks = fetch_new_bookmarks(session, cfg, state, since=since)
    if not bookmarks:
        print("No bookmarks to include." + ("" if since else " Try --hours 24 to preview recent ones."))
        return 0
    path, articles = make_book(session, cfg, bookmarks, sched.label(sched.due(now)), sched.due(now), STATE_DIR / "preview")
    print(f"{path}  ({path.stat().st_size / 1e6:.1f} MB)")
    for a in articles:
        print(f"  {a.keyword:<16} {a.title[:70]}{'' if a.body_html else '  [no full text]'}")
    print("Nothing was sent.")
    return 0


def cmd_status(args) -> int:
    try:
        cfg = load_config()
        print(f"Config: {CONFIG_PATH}")
        print(f"Kindle: {', '.join(cfg['kindle_emails'])}   via {cfg['smtp']['username']}")
        ai = cfg.get("ai", {})
        writer = ai.get("candidate_writer", "built-in")
        if ai_provider(cfg) == "openai":
            writer = ai.get("openai_candidate_writer", "built-in" if writer == "built-in" else openai_model(cfg))
        model = ((ai.get("openai_decision_model") or openai_model(cfg)) if ai_provider(cfg) == "openai"
                 else ai.get("decision_model") or DEFAULT_DECISION_MODEL)
        print("Keywords: " + (f"{ai_provider(cfg)} ({model}) chooses from "
                              f"{'built-in' if writer == 'built-in' else writer} candidates; built-in if unreachable"
                              if has_ai_key(cfg) else f"built-in word picker (no {ai_provider(cfg)} key)"))
    except SetupError as e:
        print(e)
        cfg = {}
    sched = Schedule(cfg)
    now = now_local(sched)
    if not STATE_PATH.exists():
        print("No runs yet.")
    else:
        state = load_state(sched, now)
        print(f"Last finished slot: {sched.label(parse_dt(state['last_slot_done']))}")
        print(f"New bookmarks since: {parse_dt(state['watermark']):%b %d %H:%M}")
        if state.get("retry_after"):
            print(f"Backing off after an error until {parse_dt(state['retry_after']):%H:%M}")
        for h in state["history"][-5:]:
            print(f"  sent {parse_dt(h['sent_at']):%b %d %H:%M}  {h['file']}  ({h['articles']} articles)")
    print(f"Next slot: {sched.label(sched.next(now))} at {sched.next(now):%b %d %H:%M}")
    sys.stdout.flush()
    subprocess.run(["systemctl", "--user", "list-timers", f"{APP}.timer", "--no-pager"], check=False)
    return 0


def ask(prompt: str, default: str | None = None, secret: bool = False, optional: bool = False) -> str:
    shown = f" [{'keep current' if secret else default}]" if default else ""
    while True:
        val = (getpass.getpass if secret else input)(f"{prompt}{shown}: ").strip()
        if val:
            if secret:
                print(f"     got it ({len(val)} characters)")
            return val
        if default or optional:
            return default or ""


def smtp_login(host: str, port: int, user: str, pw: str) -> None:
    ctx = ssl.create_default_context()
    server = (smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) if port == 465
              else smtplib.SMTP(host, port, timeout=30))
    with server:
        if port != 465:
            server.starttls(context=ctx)
        server.login(user, pw)


def setup_ai(cfg: dict, selected_provider: str | None = None) -> None:
    ai = cfg.setdefault("ai", {})
    selected_provider = selected_provider or ask("\nAI provider (openai/openrouter)", ai.get("provider", "openrouter"))
    if selected_provider not in ("openai", "openrouter"):
        raise SetupError("AI provider must be openai or openrouter")
    ai["provider"] = selected_provider
    if selected_provider == "openai":
        print("\nOpenAI project key: platform.openai.com/api-keys (use the organization holding your credits).")
        path = Path(ai.get("openai_api_key_file") or DEFAULT_OPENAI_KEY_FILE).expanduser()
        old_key = path.read_text().strip() if path.is_file() else ai.get("openai_api_key", "")
        key = ask("   OpenAI API key", old_key, secret=True, optional=True)
        if not key:
            print("   No key supplied. OpenAI summaries require a key before they can run.")
            return
        try:
            response = http().get(f"{OPENAI_API}/models/{openai_model(cfg)}",
                                  headers={"Authorization": f"Bearer {key}"}, timeout=20)
            if not response.ok:
                raise SetupError(f"OpenAI model access check failed (HTTP {response.status_code}); settings were not saved")
        except SetupError:
            raise
        except Exception as exc:
            raise SetupError("Could not reach OpenAI; settings were not saved") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as secret:
            secret.write(key + "\n")
        ai.pop("openai_api_key", None)
        ai["openai_api_key_file"] = str(path)
        ai.setdefault("openai_model", DEFAULT_OPENAI_MODEL)
        ai.setdefault("openai_candidate_writer", openai_model(cfg))
        print("   OpenAI key saved privately; OpenAI is selected for both services.")
        return
    print("\n5) Optional: OpenRouter API key, so Jev picks the filename keywords (openrouter.ai/keys).")
    print("   Press Enter to skip; the built-in word picker is used without it.")
    while True:
        key = ask("   OpenRouter API key", ai.get("openrouter_api_key"), secret=True, optional=True)
        if not key:
            print("   Skipped. Keywords come from the built-in word picker.")
            return
        session = http()
        try:
            r = session.get(f"{OPENROUTER_API}/key", headers={"Authorization": f"Bearer {key}"}, timeout=20)
            if r.status_code == 404:
                r = session.get(f"{OPENROUTER_API}/auth/key", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        except Exception as e:
            ai["openrouter_api_key"] = key
            print(f"   ? Couldn't reach OpenRouter ({e}). Saved anyway; the built-in picker is used until it's reachable.")
            return
        if r.status_code in (401, 403):
            print("   ✗ OpenRouter rejected that key. Copy it again from openrouter.ai/keys.")
            ai.pop("openrouter_api_key", None)
            continue
        ai["openrouter_api_key"] = key
        info = r.json().get("data", {}) if r.ok else {}
        left = info.get("limit_remaining")
        print("   ✓ OpenRouter key works" + (f" (${left:.2f} credit left)" if isinstance(left, (int, float)) else ""))
        return


def cmd_setup(args) -> int:
    try:
        with CONFIG_PATH.open("rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        cfg = {}
    cfg.setdefault("smtp", {})
    print("Raindrop -> Kindle setup. Press Enter to keep a value in [brackets].")
    print("Secret values stay hidden while you type or paste (paste with Ctrl+Shift+V).\n")
    if args.ai:
        setup_ai(cfg, args.provider)
        write_config(cfg)
        print(f"\nSaved {CONFIG_PATH}.")
        return 0

    print("1) Raindrop test token: app.raindrop.io/settings/integrations -> For Developers")
    print("   -> Create new app -> click the app -> Create test token")
    while True:
        token = ask("   Raindrop test token", cfg.get("raindrop_token"), secret=True)
        try:
            me = raindrop_get(http(), {**cfg, "raindrop_token": token}, "/user")["user"]
        except SetupError:
            print("   ✗ Raindrop rejected that token. Copy the *test token* (not the Client ID or secret) and try again.")
            cfg.pop("raindrop_token", None)
            continue
        except Exception as e:
            print(f"   ✗ {e}")
            cfg.pop("raindrop_token", None)
            continue
        print(f"   ✓ Raindrop: signed in as {me.get('fullName') or me.get('email')}")
        cfg["raindrop_token"] = token
        break

    print("\n2) Send-to-Kindle address (amazon.in -> Manage Your Content and Devices -> Preferences)")
    kindles = ask("   Kindle email(s), comma-separated", ", ".join(cfg.get("kindle_emails", [])) or None)
    cfg["kindle_emails"] = [e.strip() for e in kindles.split(",") if e.strip()]

    smtp = cfg["smtp"]
    print("\n3) Gmail account that sends the ebooks. It must be on Amazon's approved sender list.")
    smtp["username"] = ask("   Sender email", smtp.get("username"))
    smtp["host"] = ask("   SMTP host", smtp.get("host") or ("smtp.gmail.com" if smtp["username"].endswith("@gmail.com") else None))
    smtp["port"] = int(ask("   SMTP port", str(smtp.get("port", 465))))
    smtp["sender"] = smtp.get("sender") or smtp["username"]
    write_config(cfg)  # save what we have so far

    print("\n4) Gmail app password: a 16-letter code from myaccount.google.com/apppasswords")
    print(f"   (signed in as {smtp['username']}; needs 2-Step Verification). Press Enter to skip for now.")
    while True:
        pw = ask("   App password", smtp.get("password"), secret=True, optional=True).replace(" ", "")
        if not pw:
            print("   Skipped. Preview works now; run `raindrop-kindle setup` again to add the password.")
            break
        try:
            smtp_login(smtp["host"], smtp["port"], smtp["username"], pw)
        except Exception as e:
            print(f"   ✗ Gmail refused it: {e}")
            smtp.pop("password", None)
            continue
        print(f"   ✓ Email: logged in to {smtp['host']} as {smtp['username']}")
        smtp["password"] = pw
        break

    setup_ai(cfg, args.provider)
    write_config(cfg)
    print(f"\nSaved {CONFIG_PATH} (readable only by you).")
    if smtp.get("password"):
        print("All set. Batches run at "
              + ", ".join(f"{v} ({k})" for k, v in cfg.get("schedule", {}).get("slots", DEFAULT_SLOTS).items())
              + " India time. Check progress with `raindrop-kindle status`.")
    print("Try `raindrop-kindle preview --hours 24` to build a sample ebook without sending it.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog=APP, description="Send new Raindrop bookmarks to Kindle as EPUB batches.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("setup", help="enter Raindrop, Kindle and email settings")
    st.add_argument("--ai", action="store_true", help="only configure the AI provider and API key")
    st.add_argument("--provider", choices=("openai", "openrouter"), help="select the AI provider")
    sub.add_parser("tick", help="run a batch if one is due (called by the systemd timer)")
    sub.add_parser("send-now", help="send all unsent bookmarks now, without waiting for a slot")
    pv = sub.add_parser("preview", help="build the next ebook locally without sending it")
    pv.add_argument("--hours", type=float, help="include everything saved in the last N hours")
    sub.add_parser("status", help="show schedule, last batches and timer")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s" if not sys.stderr.isatty() else "%(message)s")
    for noisy in ("trafilatura", "htmldate", "courlan", "urllib3", "charset_normalizer", "PIL"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    try:
        return {"setup": cmd_setup, "tick": cmd_tick, "send-now": cmd_send_now,
                "preview": cmd_preview, "status": cmd_status}[args.cmd](args)
    except SetupError as e:
        print(e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
