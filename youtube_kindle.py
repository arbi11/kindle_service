#!/usr/bin/env python3
"""YouTube -> Kindle.

Watches a hand-picked list of YouTube channels, summarises every new regular
video (no Shorts, no live streams) with an LLM that focuses on the reader's
interests, and emails one short EPUB per batch to Kindle.

Runs on this laptop from a systemd user timer, like raindrop-kindle, and
shares its email, Kindle and AI provider settings. Nothing touches the network
until a book is due: the timer's 5-minute tick is only a clock check. Batch rules are the same:
missed slots collapse into one book, a slot under 30 minutes away runs right
after boot/wake, nothing is sent twice and empty batches send nothing.

Pipeline per video:
  1. New uploads come from each channel's public RSS feed.
  2. Shorts and live streams are skipped (feed link and yt-dlp metadata).
  3. Transcript: YouTube captions when available, otherwise the audio is
     transcribed locally with faster-whisper.
  4. Jev decides how long each video's story is (1-2 Kindle pages), based on the
     reader's interests, before anything is written.
  5. A chat model retells each video as a short story in its own arc and
     voice, written to that length. The transcript never goes into the book.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
import tomllib
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from html import escape, unescape
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import raindrop_kindle as rk  # noqa: E402  shared email, EPUB naming and AI helpers
from ai_client import DEFAULT_OPENAI_TRANSCRIBE_MODEL, transcribe as ai_transcribe

APP = "youtube-kindle"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP
CONFIG_PATH = CONFIG_DIR / "config.toml"
CHANNELS_PATH = CONFIG_DIR / "channels.json"
STATE_PATH = STATE_DIR / "state.json"
TRANSCRIPTS_DIR = STATE_DIR / "transcripts"
BOOKS_DIR = STATE_DIR / "books"
LOCK_PATH = STATE_DIR / ".lock"

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}
DEFAULT_SLOTS = {"M": "10:00 Asia/Kolkata", "U": "08:30 America/New_York"}
DISPLAY_TZ = ZoneInfo("Asia/Kolkata")
WORDS_PER_PAGE = 260
CAPTION_GRACE = timedelta(minutes=90)  # auto-captions can lag behind an upload
WAKE_GAP = timedelta(minutes=12)
FAILURE_BACKOFF = timedelta(minutes=20)
MAX_TRANSCRIPT_WORDS = 45000
DEFAULT_SUMMARY_MODEL = "z-ai/glm-5.3-flash"
DEFAULT_EDITOR_MODEL = "anthropic/claude-haiku-5.5"  # trims long drafts; GLM won't edit itself down
WRITER_STRETCH = 0.66  # GLM writes ~1.5x the length it is asked for

log = logging.getLogger(APP)


# --------------------------------------------------------------------------
# Config, channels, state
# --------------------------------------------------------------------------

CONFIG_TEMPLATE = '''# YouTube -> Kindle settings. Email, Kindle address and AI provider are shared
# with raindrop-kindle (~/.config/raindrop-kindle/config.toml).

# What the summaries should focus on. Plain words are fine; one topic per line.
interests = """
{interests}
"""

# Length of each video's story in Kindle pages (about {wpp} words per page).
# Jev picks a length in this range for each video, based on your interests.
min_pages_per_video = {min_pages}
max_pages_per_video = {max_pages}

# OpenRouter model that writes the stories (GLM 5.3 Flash: ~$0.001 per video), and the
# one that trims a story that runs well past its length (Claude Haiku: ~$0.0007 per edit)
summary_model = "{summary_model}"
editor_model = "{editor_model}"
# Models used when the shared AI provider is OpenAI.
openai_summary_model = "{openai_summary_model}"
openai_editor_model = "{openai_editor_model}"

[schedule]
bring_forward_minutes = 30

[schedule.slots]
# Letter = "HH:MM Timezone". U = before the US market opens (9:30 New York time).
{slots}

[transcribe]
# When YouTube captions aren't available: a fast cloud transcriber on OpenRouter
# (~$0.034 per hour of audio). It sends at most the first N minutes of any video and
# at most M minutes per run; Whisper on this laptop is the backup.
cloud_model = "{cloud_model}"
openai_model = "{openai_transcribe_model}"
cloud_max_minutes_per_video = {cloud_per_video}
cloud_minutes_per_run = {cloud_minutes}
whisper_model = "{whisper_model}"
threads = {threads}
'''


def write_config(cfg: dict) -> None:
    sched = cfg.get("schedule", {})
    tr = cfg.get("transcribe", {})
    text = CONFIG_TEMPLATE.format(
        interests=cfg.get("interests", "").strip() or "(write your interests here)",
        wpp=WORDS_PER_PAGE,
        min_pages=cfg.get("min_pages_per_video", 1),
        max_pages=cfg.get("max_pages_per_video", 2),
        summary_model=cfg.get("summary_model", DEFAULT_SUMMARY_MODEL),
        editor_model=cfg.get("editor_model", DEFAULT_EDITOR_MODEL),
        openai_summary_model=cfg.get("openai_summary_model", rk.DEFAULT_OPENAI_MODEL),
        openai_editor_model=cfg.get("openai_editor_model", rk.DEFAULT_OPENAI_MODEL),
        slots="\n".join(f'{k} = "{v}"' for k, v in sched.get("slots", DEFAULT_SLOTS).items()),
        cloud_model=tr.get("cloud_model", "google/gemini-2.5-flash-lite"),
        openai_transcribe_model=tr.get("openai_model", DEFAULT_OPENAI_TRANSCRIBE_MODEL),
        cloud_per_video=int(tr.get("cloud_max_minutes_per_video", 30)),
        cloud_minutes=int(tr.get("cloud_minutes_per_run", 90)),
        whisper_model=tr.get("whisper_model", "small"),
        threads=int(tr.get("threads", 4)),
    )
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, CONFIG_PATH)


def load_config() -> dict:
    """YouTube settings merged with the shared raindrop-kindle email/AI settings."""
    if not CONFIG_PATH.exists():
        write_config({})
    with CONFIG_PATH.open("rb") as f:
        cfg = tomllib.load(f)
    shared = rk.load_config()
    if not rk.has_ai_key(shared):
        raise rk.SetupError(f"Summaries need the {rk.ai_provider(shared)} key. Run `raindrop-kindle setup --ai`.")
    cfg["shared"] = shared
    return cfg


def text_model(cfg: dict, role: str = "summary") -> str:
    if rk.ai_provider(cfg["shared"]) == "openai":
        return cfg.get(f"openai_{role}_model") or rk.openai_model(cfg["shared"])
    default = DEFAULT_EDITOR_MODEL if role == "editor" else DEFAULT_SUMMARY_MODEL
    return cfg.get(f"{role}_model", default)


def interests(cfg: dict) -> str:
    text = cfg.get("interests", "").strip()
    return "" if text.startswith("(write your interests") else text


def load_channels() -> list[dict]:
    return json.loads(CHANNELS_PATH.read_text()) if CHANNELS_PATH.exists() else []


def save_channels(channels: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CHANNELS_PATH.write_text(json.dumps(channels, indent=2, ensure_ascii=False))


class Schedule:
    """Slots that can each live in their own timezone (10:00 India, 08:30 New York)."""

    def __init__(self, cfg: dict):
        sched = cfg.get("schedule", {})
        self.slots = []
        for letter, spec in sched.get("slots", DEFAULT_SLOTS).items():
            t, _, tz = spec.strip().partition(" ")
            self.slots.append((letter, dtime.fromisoformat(t), ZoneInfo(tz.strip() or "Asia/Kolkata")))
        self.tz = DISPLAY_TZ
        self.bring_forward = timedelta(minutes=sched.get("bring_forward_minutes", 30))

    def _around(self, now: datetime):
        for _, t, tz in self.slots:
            day = now.astimezone(tz).date()
            for offset in (-1, 0, 1):
                yield datetime.combine(day + timedelta(days=offset), t, tz)

    def due(self, now: datetime) -> datetime:
        return max(s for s in self._around(now) if s <= now)

    def next(self, now: datetime) -> datetime:
        return min(s for s in self._around(now) if s > now)

    def label(self, slot: datetime) -> str:
        letter = next((k for k, t, tz in self.slots if slot.astimezone(tz).time() == t), "X")
        return f"{slot.astimezone(self.tz):%b%d}_YT_{letter}"


def now_local() -> datetime:
    return datetime.now(DISPLAY_TZ).replace(microsecond=0)


def load_state(sched: Schedule, now: datetime) -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    due = sched.due(now).isoformat()
    return {"watermark": due, "last_slot_done": due, "last_tick": None, "last_poll": None,
            "pending_open": False, "retry_after": None, "videos": {}, "history": []}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = (now_local() - timedelta(days=30)).isoformat()
    state["videos"] = {k: v for k, v in state["videos"].items()
                       if v.get("status") not in ("sent", "skipped") or v.get("published", "") >= cutoff}
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    os.replace(tmp, STATE_PATH)


class Lock:
    def __init__(self, wait: bool):
        self.wait = wait

    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.f = LOCK_PATH.open("w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX if self.wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.f.close()
            return False
        return True

    def __exit__(self, *exc):
        if not self.f.closed:
            self.f.close()


def online(timeout: int = 90) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            socket.create_connection(("www.youtube.com", 443), timeout=5).close()
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(10)


# --------------------------------------------------------------------------
# Channels
# --------------------------------------------------------------------------

def resolve_channel(session, query: str) -> dict:
    """Turn a channel URL, @handle or name into {id, name, handle}."""
    q = query.strip()
    if m := re.search(r"(UC[\w-]{22})", q):
        url = f"https://www.youtube.com/channel/{m.group(1)}"
    elif q.startswith("http"):
        url = q
    elif q.startswith("@"):
        url = f"https://www.youtube.com/{q}"
    else:
        r = session.get("https://www.youtube.com/results", params={"search_query": q, "sp": "EgIQAg%3D%3D"},
                        headers={"Accept-Language": "en-US,en"}, timeout=20)
        ids = re.findall(r'"channelRenderer":\{"channelId":"(UC[\w-]{22})"', r.text)
        if not ids:
            raise rk.SetupError(f"No YouTube channel found for {q!r}")
        url = f"https://www.youtube.com/channel/{ids[0]}"
    r = session.get(url, headers={"Accept-Language": "en-US,en"}, timeout=20)
    r.raise_for_status()
    # The page mentions related channels too; the canonical link is the channel itself.
    cid = (re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', r.text)
           or re.search(r'"externalId":"(UC[\w-]{22})"', r.text)
           or re.search(r'<meta itemprop="identifier" content="(UC[\w-]{22})"', r.text))
    name = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
    handle = re.search(r'"canonicalBaseUrl":"/(@[^"]+)"', r.text)
    if not cid:
        raise rk.SetupError(f"Couldn't read the channel at {url}")
    return {"id": cid.group(1), "name": unescape(name.group(1)) if name else cid.group(1),
            "handle": handle.group(1) if handle else ""}


# --------------------------------------------------------------------------
# Finding videos
# --------------------------------------------------------------------------

def feed_entries(session, ch: dict) -> list[tuple[str, datetime, dict]]:
    """A channel's latest uploads from its RSS feed (YouTube lists the newest 15)."""
    try:
        r = session.get(FEED_URL.format(ch["id"]), timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        if rk.is_offline_error(e):
            raise rk.Offline(f"YouTube unreachable: {e}") from e
        raise
    out = []
    for entry in root.findall("a:entry", NS):
        vid = entry.findtext("yt:videoId", namespaces=NS)
        if not vid:
            continue
        published = datetime.fromisoformat(entry.findtext("a:published", namespaces=NS))
        group = entry.find("media:group", NS)
        video = {
            "channel": ch["name"], "channel_id": ch["id"],
            "title": entry.findtext("a:title", namespaces=NS),
            "published": published.astimezone(DISPLAY_TZ).isoformat(),
            "description": (group.findtext("media:description", namespaces=NS) or "")[:1500] if group is not None else "",
            "status": "new",
        }
        if "/shorts/" in entry.find("a:link", NS).get("href", ""):
            video.update(status="skipped", reason="short")
        out.append((vid, published, video))
    return out


def poll_feeds(session, channels: list[dict], state: dict, since: datetime) -> int:
    """Record new uploads from each channel's feed. Returns how many were added.

    A feed that fails is retried once; if it still fails, the channel is noted in
    state["feed_failures"], and the next time its feed works the channel's Videos tab is
    checked back to the failure, in case an upload fell out of the feed's newest 15.
    """
    failures = state.setdefault("feed_failures", {})
    added = 0
    for ch in channels:
        entries, error = None, None
        for attempt in (1, 2):
            try:
                entries = feed_entries(session, ch)
                break
            except rk.Offline:
                raise
            except Exception as e:
                error = e
                if attempt == 1:
                    time.sleep(15)
        if entries is None:
            failures.setdefault(ch["id"], now_local().isoformat())
            log.warning("Feed for %s failed twice (%s); its videos will be picked up on a later run",
                        ch["name"], str(error).split(" for url")[0])
            continue
        failed_since = failures.pop(ch["id"], None)
        for vid, published, video in entries:
            if vid in state["videos"] or published <= since:
                continue
            state["videos"][vid] = video
            added += 1
            log.info("New %s from %s: %s", "Short (skipped)" if video["status"] == "skipped" else "video",
                     ch["name"], video["title"])
        oldest = min((published for _, published, _ in entries), default=None)
        if failed_since and oldest is not None and oldest > rk.parse_dt(failed_since) - timedelta(hours=12):
            # The feed was down and its newest 15 don't reach back far enough: check the Videos tab too.
            known = set(state["videos"]) | {vid for vid, _, _ in entries}
            back_to = max(since, rk.parse_dt(failed_since) - timedelta(hours=12))
            for vid, video in tab_videos(ch, back_to, now_local(), known).items():
                state["videos"][vid] = video
                added += 1
                log.info("Caught up %s from %s (missed while its feed was down)", video["title"], ch["name"])
    return added


class _QuietYtdlp:
    """yt-dlp prints errors (members-only videos and so on) even when quiet; route them to our log."""

    def debug(self, msg):
        pass

    warning = debug

    def error(self, msg):
        log.debug("yt-dlp: %s", msg)


def video_details(video_id: str) -> dict | None:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "logger": _QuietYtdlp()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except Exception as e:
        reason = str(e).splitlines()[0]
        reason = "members-only" if "members" in reason else reason[:200]
        log.warning("Couldn't read details for %s: %s", video_id, reason)
        return None
    return {k: info.get(k) for k in ("duration", "live_status", "media_type", "width", "height", "chapters",
                                     "title", "description", "timestamp")}


def classify(video_id: str, video: dict) -> None:
    """Skip Shorts and live streams; leave upcoming/live ones for later."""
    if video.get("checked"):
        return
    info = video_details(video_id)
    if info is None:
        return  # try again next time; the transcript step can still go ahead
    apply_details(video, info)


def apply_details(video: dict, info: dict) -> None:
    video["checked"] = True
    video["duration"] = info.get("duration")
    live = info.get("live_status")
    if live in ("is_upcoming", "is_live"):
        video["checked"] = False  # look again once it has aired
        video["status"] = "waiting"
        return
    if live in ("was_live", "post_live") or info.get("media_type") == "livestream":
        video.update(status="skipped", reason="live stream")
    elif info.get("media_type") == "short" or (
            (info.get("duration") or 999) <= 180 and (info.get("height") or 0) > (info.get("width") or 1)):
        video.update(status="skipped", reason="short")
    elif video["status"] == "waiting":
        video["status"] = "new"
    if info.get("chapters"):
        video["chapters"] = [(c.get("start_time", 0), c.get("title", "")) for c in info["chapters"]][:40]


# --------------------------------------------------------------------------
# Transcripts
# --------------------------------------------------------------------------

def transcript_path(video_id: str) -> Path:
    return TRANSCRIPTS_DIR / f"{video_id}.json"


class CaptionsBlocked(Exception):
    """YouTube is rate-limiting caption downloads from this connection (temporary)."""


_last_caption_request = 0.0
CAPTION_PAUSE = 2.5  # seconds between caption downloads, so YouTube doesn't block us


def fetch_captions(video_id: str) -> tuple[list, str] | None:
    """YouTube captions (manual or auto). None when the video has none."""
    global _last_caption_request
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import IpBlocked, NoTranscriptFound, RequestBlocked, TranscriptsDisabled

    wait = _last_caption_request + CAPTION_PAUSE - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_caption_request = time.monotonic()
    api = YouTubeTranscriptApi()
    try:
        listing = api.list(video_id)
        try:
            tr = listing.find_transcript(["en", "en-US", "en-GB", "hi"])
        except NoTranscriptFound:
            tr = next(iter(listing))
        lines = [(s.start, s.text) for s in tr.fetch()]
    except (NoTranscriptFound, TranscriptsDisabled, StopIteration):
        return None
    except (IpBlocked, RequestBlocked) as e:
        raise CaptionsBlocked(video_id) from e
    return lines, tr.language_code


_youtube_blocked = False  # once YouTube refuses captions, stop asking it for the rest of the run


def download_audio(video_id: str) -> Path:
    import yt_dlp

    audio_dir = CACHE_DIR / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    cached = next((f for f in audio_dir.glob(f"{video_id}.*") if not f.name.endswith(".part")), None)
    if cached:
        return cached
    opts = {"format": "bestaudio[ext=m4a]/bestaudio", "outtmpl": str(audio_dir / f"{video_id}.%(ext)s"),
            "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "logger": _QuietYtdlp()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        return Path(ydl.prepare_filename(info))


def audio_minutes(path: Path) -> float:
    """Length of an audio file; 0 when ffprobe can't tell (it sometimes reports "N/A")."""
    for entry in ("format=duration", "stream=duration"):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entry, "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=60)
        for value in out.stdout.split():
            try:
                return float(value) / 60
            except ValueError:
                continue
    return 0.0


CLOUD_PROMPT = """Transcribe this audio verbatim in the language spoken. Start a new line about every
30 seconds; begin each line with the time from the start of this clip as [mm:ss].
Output only the transcript lines."""


class CloudBudget:
    """Caps how many minutes of audio one run sends to the cloud transcriber."""

    def __init__(self, minutes: float):
        self.left = minutes


def cloud_transcribe(session, cfg: dict, audio: Path, max_minutes: float | None = None) -> tuple[list, str]:
    """Fast transcription by an audio-capable model on OpenRouter, in 10-minute pieces.

    Only the first max_minutes of audio are sent, to keep the cost per video bounded.
    """
    if rk.ai_provider(cfg["shared"]) == "openai":
        return openai_cloud_transcribe(session, cfg, audio, max_minutes)
    import base64
    import tempfile

    model = cfg.get("transcribe", {}).get("cloud_model", "google/gemini-2.5-flash-lite")
    started = time.monotonic()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE_DIR) as tmp:
        limit = ["-t", str(int(max_minutes * 60))] if max_minutes else []
        # -t before -i limits what is read; as an output option the segment muxer ignores it
        subprocess.run(["ffmpeg", "-loglevel", "error", *limit, "-i", str(audio), "-ac", "1", "-ar", "16000", "-b:a", "32k",
                        "-f", "segment", "-segment_time", "600", "-reset_timestamps", "1",
                        str(Path(tmp) / "part%03d.mp3")], check=True, timeout=600)
        parts = [f for f in sorted(Path(tmp).glob("part*.mp3")) if f.stat().st_size > 8000]  # drop empty tails

        def one(n_part):
            n, part = n_part
            data = rk.ai_chat(session, cfg["shared"], {
                "model": model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": CLOUD_PROMPT},
                    {"type": "input_audio", "input_audio": {"data": base64.b64encode(part.read_bytes()).decode(),
                                                            "format": "mp3"}}]}],
                "temperature": 0,
                "max_tokens": 8000,
                "usage": {"include": True},
            })
            text = data["choices"][0]["message"]["content"] or ""
            pieces = []
            for m in re.finditer(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*(.+)", text):
                a, b, c, said = m.groups()
                secs = int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
                pieces.append((secs, said.strip()))
            if not pieces and text.strip():
                pieces = [(0, text.strip())]
            length = audio_minutes(part) * 60 or part.stat().st_size * 8 / 32000  # 32 kbps pieces
            times = [t for t, _ in pieces]
            sane = times == sorted(times) and times and times[-1] <= length + 15 and times[-1] >= length * 0.6
            if not sane:
                # The model's clock drifted: space lines by how much was said, which tracks time closely.
                counts = [len(said.split()) for _, said in pieces]
                total, run, times = max(1, sum(counts)), 0, []
                for c in counts:
                    times.append(length * run / total)
                    run += c
            lines = [(n * 600 + t, said) for t, (_, said) in zip(times, pieces)]
            return lines, (data.get("usage") or {}).get("cost") or 0.0

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(one, enumerate(parts)))
    lines = [line for part_lines, _ in results for line in part_lines]
    if not lines:
        raise RuntimeError("cloud transcriber returned nothing")
    log.info("Transcribed via %s in %.0f s ($%.4f)", model, time.monotonic() - started, sum(c for _, c in results))
    return lines, "auto"


def openai_cloud_transcribe(session, cfg: dict, audio: Path,
                            max_minutes: float | None = None) -> tuple[list, str]:
    """Use native file transcription with a timestamp from each 30-second clip.

    GPT-Transcribe returns text without segment timestamps. Short audio clips keep
    timestamp links anchored to the audio rather than asking a model to invent times.
    """
    import tempfile

    model = cfg.get("transcribe", {}).get("openai_model") or DEFAULT_OPENAI_TRANSCRIBE_MODEL
    segment_seconds = 30
    started = time.monotonic()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE_DIR) as tmp:
        limit = ["-t", str(int(max_minutes * 60))] if max_minutes else []
        subprocess.run(["ffmpeg", "-loglevel", "error", *limit, "-i", str(audio), "-ac", "1", "-ar", "16000", "-b:a", "32k",
                        "-f", "segment", "-segment_time", str(segment_seconds), "-reset_timestamps", "1",
                        str(Path(tmp) / "part%05d.mp3")], check=True, timeout=600)
        # MP3 padding can create a tiny extra clip exactly at the requested cap.
        parts = [part for n, part in enumerate(sorted(Path(tmp).glob("part*.mp3")))
                 if max_minutes is None or n * segment_seconds < max_minutes * 60]

        def one(item):
            n, part = item
            data = ai_transcribe(session, cfg["shared"], part, model)
            text = data.get("text", "").strip()
            languages = data.get("languages") or []
            lang = languages[0].get("code", "auto") if languages else "auto"
            return ([(n * segment_seconds, text)] if text else []), lang

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(one, enumerate(parts)))
    lines = [line for chunk, _ in results for line in chunk]
    if not lines:
        raise RuntimeError("OpenAI returned an empty transcript")
    language = next((lang for _, lang in results if lang != "auto"), "auto")
    log.info("Transcribed via %s in %.0f s (%d clips; cost in OpenAI usage dashboard)",
             model, time.monotonic() - started, len(parts))
    return lines, language


def whisper_transcribe(audio: Path, cfg: dict) -> tuple[list, str]:
    from faster_whisper import WhisperModel

    tr_cfg = cfg.get("transcribe", {})
    started = time.monotonic()
    model = WhisperModel(tr_cfg.get("whisper_model", "small"), device="cpu", compute_type="int8",
                         cpu_threads=int(tr_cfg.get("threads", 4)), download_root=str(CACHE_DIR / "whisper"))
    # Decode with ffmpeg ourselves: faster-whisper's PyAV path breaks on newer PyAV releases.
    import numpy as np
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(audio), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True, timeout=1800).stdout
    segments, meta = model.transcribe(np.frombuffer(pcm, np.float32), vad_filter=True, beam_size=1)
    lines = [(seg.start, seg.text.strip()) for seg in segments]
    log.info("Transcribed locally with Whisper in %.0f min (%s)", (time.monotonic() - started) / 60, meta.language)
    return lines, meta.language


def prepare_transcript(video_id: str, video: dict, cfg: dict, allow_whisper: bool, now: datetime,
                       session=None, budget: "CloudBudget | None" = None) -> str:
    """Get the video a transcript on disk.

    Order: YouTube captions -> cloud transcription (fast, ~$0.034/hour of audio) ->
    Whisper on this laptop (backup). Returns "ready", "whispered" (via Whisper),
    "failed" (transcription tried and failed) or "waiting" (try again next run).
    """
    path = transcript_path(video_id)
    if path.exists():
        video["status"] = "ready"
        return "ready"
    global _youtube_blocked
    blocked, got, source = _youtube_blocked, None, "captions"
    if not blocked:
        try:
            got = fetch_captions(video_id)
        except CaptionsBlocked:
            blocked = _youtube_blocked = True
            log.warning("YouTube is blocking caption downloads; not asking it again this run")
        except Exception as e:
            if rk.is_offline_error(e):
                raise rk.Offline(str(e)) from e
            log.warning("Captions for %s failed: %s", video_id, str(e).splitlines()[0][:200])
    if got is None:
        age = now - datetime.fromisoformat(video["published"])
        if not blocked and age < CAPTION_GRACE:
            return "waiting"  # auto-captions usually appear within an hour or so
        audio = None
        try:
            audio = download_audio(video_id)
            minutes = audio_minutes(audio) or (video.get("duration") or 0) / 60
            per_video = float(cfg.get("transcribe", {}).get("cloud_max_minutes_per_video", 30))
            sent = min(minutes, per_video)
            if session is not None and budget is not None and sent <= budget.left:
                try:
                    got = cloud_transcribe(session, cfg, audio, max_minutes=per_video)
                    budget.left -= sent
                    source = "cloud"
                    if minutes > per_video:
                        video["partial_minutes"] = round(per_video)  # summary covers only the start
                        log.info("Only the first %d of %.0f min were transcribed (per-video cap)", per_video, minutes)
                except Exception as e:
                    log.warning("Cloud transcription of %r failed: %s", video["title"][:50], e)
            # Captions exist but are blocked: wait for the block to lift, unless the video has waited a day.
            if got is None and allow_whisper and (not blocked or age > timedelta(days=1)):
                log.info("Transcribing %r on this laptop (%.0f min of audio)", video["title"][:50], minutes)
                got = whisper_transcribe(audio, cfg)
                source = "whisper"
        except Exception as e:
            video["transcribe_failures"] = video.get("transcribe_failures", 0) + 1
            log.warning("Transcribing %s failed: %s", video_id, e)
            if video["transcribe_failures"] >= 3:
                video.update(status="skipped", reason="no transcript")
            return "failed"
        finally:
            if got is not None and audio is not None:
                audio.unlink(missing_ok=True)
        if got is None:
            return "waiting"
    lines, lang = got
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": source, "language": lang, "lines": lines}, ensure_ascii=False))
    video["status"] = "ready"
    return "whispered" if source == "whisper" else "ready"


def transcript_text(video_id: str, every: float = 30.0) -> tuple[str, int]:
    """Transcript as "[mm:ss] text" lines, about one line per 30 s, and its word count."""
    data = json.loads(transcript_path(video_id).read_text())
    out, buf, mark = [], [], None
    for start, text in data["lines"]:
        if mark is None:
            mark = start
        buf.append(text.replace("\n", " "))
        if start - mark >= every:
            out.append(f"[{fmt_ts(mark)}] {' '.join(buf)}")
            buf, mark = [], None
    if buf:
        out.append(f"[{fmt_ts(mark or 0)}] {' '.join(buf)}")
    text = "\n".join(out)
    words = text.split()
    if len(words) > MAX_TRANSCRIPT_WORDS:
        text = " ".join(words[:MAX_TRANSCRIPT_WORDS]) + "\n[transcript truncated]"
    return text, len(words)


def ts_seconds(ts: str) -> int:
    return sum(int(v) * 60 ** n for n, v in enumerate(reversed(ts.split(":"))))


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------

SUMMARY_PROMPT = """You are writing one chapter of a Kindle digest. Retell this YouTube video as a
short, well-written story, so the reader gets what it was like to watch it.

What this reader cares about:
{interests}

How to write it:
- Follow the video's own arc: how it opens (the hook or question), how the story or
  argument builds, the turning points and evidence, and how it lands (the conclusion
  or the creator's verdict). Keep the order the creator chose.
- Keep the creator's voice: their framing, attitude and humour. Quote a few short,
  memorable lines word for word, in quotation marks.
- Write flowing paragraphs, never bullet points. Each paragraph should lead into the
  next, so the reader can follow why one thing leads to another.
- Be concrete: keep the specific numbers, names, tickers, dates, examples and stories
  that make the video convincing. Never write filler like "he discusses several factors".
- Give more room to the parts that matter to this reader, without distorting the video.
- Skip sponsor reads, merch plugs, channel housekeeping and "like and subscribe".
- Length: about {words} words, and never more than {ceiling}. Fit the whole arc into that
  length; compress, never cut off. The chapter must read as complete.
- Begin each paragraph with the [mm:ss] timestamp where that part of the video starts.
- Write in English even if the video isn't.

"takeaway" is one sentence: the single most useful thing in this video for this reader.

Reply with JSON only: {{"takeaway": "...", "paragraphs": ["[0:00] ...", "[4:10] ..."]}}"""


@dataclass
class Summary:
    video_id: str
    video: dict
    takeaway: str = ""
    paragraphs: list[str] = field(default_factory=list)
    cost: float | None = None
    keyword: str = ""

    def words(self) -> int:
        """Words in this video's chapter (its takeaway lives on the overview page)."""
        return len(self.video["title"].split()) + 8 + sum(len(p.split()) for p in self.paragraphs)


def summarise(session, cfg: dict, video_id: str, video: dict, words: int) -> Summary:
    text, n_words = transcript_text(video_id)
    chapters = "\n".join(f"[{fmt_ts(t)}] {title}" for t, title in video.get("chapters", []))
    partial = (f"Note: the transcript covers only the first {video['partial_minutes']} minutes of the video.\n"
               if video.get("partial_minutes") else "")
    user = (partial + f"Channel: {video['channel']}\nTitle: {video['title']}\n"
            f"Published: {video['published'][:10]}\nDescription: {video.get('description', '')[:600]}\n"
            + (f"Chapters:\n{chapters}\n" if chapters else "") + f"\nTranscript:\n{text}")
    schema = {"type": "object", "additionalProperties": False, "required": ["takeaway", "paragraphs"], "properties": {
        "takeaway": {"type": "string"},
        "paragraphs": {"type": "array", "items": {"type": "string"}}}}
    stretch = 1.0 if rk.ai_provider(cfg["shared"]) == "openai" else WRITER_STRETCH
    data = rk.ai_chat(session, cfg["shared"], {
        "model": text_model(cfg),
        "messages": [{"role": "system", "content": SUMMARY_PROMPT.format(
                          interests=interests(cfg) or "(not given: tell the story faithfully)",
                          words=int(words * stretch), ceiling=int(words * stretch * 1.2))},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "summary", "strict": True, "schema": schema}},
        "reasoning": {"effort": "low"},
        "temperature": 0.5,
        "max_tokens": 6000 + words * 3,
        "usage": {"include": True},
    })
    content = re.sub(r"^```(?:json)?|```$", "", (data["choices"][0]["message"]["content"] or "").strip()).strip()
    parsed = json.loads(content)
    s = Summary(video_id, video, takeaway=parsed.get("takeaway", "").strip(),
                paragraphs=[p.strip() for p in parsed.get("paragraphs", []) if p.strip()],
                cost=rk.usage_cost(data))
    if not s.takeaway or not s.paragraphs:
        raise RuntimeError("Summary is missing its takeaway or paragraphs")
    if s.words() > words * 1.35:
        tighten(session, cfg, s, words)
    last = json.loads(transcript_path(video_id).read_text())["lines"][-1][0] + 30
    s.paragraphs = [re.sub(r"^\[(\d{1,2}(?::\d{2}){1,2})\]\s*", lambda m: m.group(0) if ts_seconds(m.group(1)) <= last else "", p)
                    for p in s.paragraphs]
    log.info("Summarised %r (%d transcript words -> %d of %d; %s)", video["title"][:60], n_words,
             s.words(), words, rk.cost_label(s.cost))
    return s


def tighten(session, cfg: dict, s: Summary, words: int) -> None:
    """One editing pass when a chapter runs well past its length: same story, fewer words."""
    draft = "\n\n".join(s.paragraphs)
    try:
        data = rk.ai_chat(session, cfg["shared"], {
            "model": text_model(cfg, "editor"),
            "messages": [{"role": "user", "content": (
                f"The chapter below is {len(draft.split())} words. Rewrite it in at most {words} words, which means "
                f"cutting about {len(draft.split()) - words} words. Do it by dropping secondary details and examples "
                "and merging sentences. Keep the story's arc and ending, the creator's voice, the best one or two "
                "quotes, the most important numbers, and the [mm:ss] timestamp at the start of each paragraph. "
                "It must still read as a complete story. Reply with JSON only: "
                '{"paragraphs": ["[0:00] ...", "..."]}\n\n' + draft)}],
            "response_format": {"type": "json_object"},
            "reasoning": {"effort": "low"},
            "temperature": 0.3,
            "max_tokens": 4000 + words * 3,
            "usage": {"include": True},
        })
        content = re.sub(r"^```(?:json)?|```$", "", (data["choices"][0]["message"]["content"] or "").strip()).strip()
        paragraphs = [p.strip() for p in json.loads(content).get("paragraphs", []) if p.strip()]
    except Exception as e:
        log.warning("Couldn't tighten %r (%s); keeping the longer version", s.video["title"][:50], e)
        return
    s.cost = rk.total_cost([s.cost, rk.usage_cost(data)])
    if paragraphs and sum(len(p.split()) for p in paragraphs) < len(draft.split()):
        log.info("Edited %r from %d to %d words", s.video["title"][:50], len(draft.split()),
                 sum(len(p.split()) for p in paragraphs))
        s.paragraphs = paragraphs


SPACE = ["About one page", "A little over a page", "Closer to two pages", "Two full pages"]


def plan_lengths(session, cfg: dict, ready: list[tuple[str, dict]]) -> dict[str, int]:
    """Choose words per video using the AI provider; fall back to transcript length."""
    lo_pages = float(cfg.get("min_pages_per_video", 1))
    hi_pages = float(cfg.get("max_pages_per_video", 2))
    scores = {}
    shared = cfg["shared"]
    try:
        state = {"reader_interests": interests(cfg) or "general",
                 "videos": [{"id": vid, "channel": v["channel"], "title": v["title"],
                             "description": v.get("description", "")[:300],
                             "opening": transcript_text(vid)[0][:700]} for vid, v in ready]}
        questions = {f"v_{vid}": {
            "type": "score",
            "instructions": (f"Video {vid} ({v['title']!r}): how much space should its story get in the reader's "
                             "digest, given their interests and how much substance the video has?"),
            "criteria": SPACE} for vid, v in ready}
        data = rk.ai_decisions(session, shared, state, questions)
        for name, ans in data["answers"].items():
            scores[name[2:]] = max(0.0, min(3.0, float(ans.get("score", 1.5)))) / 3
        log.info("%s planned each video's length (%s)", rk.ai_provider(shared), rk.cost_label(rk.usage_cost(data)))
    except Exception as e:
        log.warning("AI planning unavailable (%s); sizing by video length", e)
    for vid, _ in ready:
        if vid not in scores:  # longer videos get more room: 2,000 words of transcript or less -> 1 page
            scores[vid] = max(0.0, min(1.0, (transcript_text(vid)[1] - 2000) / 6000))
    pages = {vid: lo_pages + (hi_pages - lo_pages) * sc for vid, sc in scores.items()}
    return {vid: int(p * WORDS_PER_PAGE) for vid, p in pages.items()}


# --------------------------------------------------------------------------
# Book
# --------------------------------------------------------------------------

def point_html(video_id: str, point: str) -> str:
    def link(m):
        parts = [int(x) for x in m.group(1).split(":")]
        secs = sum(v * 60 ** n for n, v in enumerate(reversed(parts)))
        return f'<a class="ts" href="https://youtu.be/{video_id}?t={secs}">{m.group(1)}</a>'
    return re.sub(r"\[(\d{1,2}(?::\d{2}){1,2})\]", link, escape(point))


def chapter_html(s: Summary) -> str:
    v = s.video
    published = datetime.fromisoformat(v["published"])
    meta = [v["channel"], f"{published:%b %d}"]
    if v.get("duration"):
        meta.append(f"{round(v['duration'] / 60)} min")
    if v.get("partial_minutes"):
        meta.append(f"summary covers the first {v['partial_minutes']} min")
    out = [f"<h1>{escape(v['title'])}</h1>",
           f'<p class="meta">{escape(" · ".join(meta))}</p>',
           f'<p class="source"><a href="https://www.youtube.com/watch?v={s.video_id}">youtube.com/watch?v={s.video_id}</a></p>']
    out += [f"<p>{point_html(s.video_id, p)}</p>" for p in s.paragraphs]
    return "<div>" + "".join(out) + "</div>"


def overview_html(summaries: list[Summary], label: str) -> str:
    items = "".join(f"<li><b>{escape(s.video['channel'])}</b>: {escape(s.video['title'])}. "
                    f"{escape(s.takeaway)}</li>" for s in summaries)
    return f"<div><h1>{escape(label)}</h1><p class=\"meta\">{len(summaries)} videos</p><ul>{items}</ul></div>"


def email_body(name: str, summaries: list[Summary]) -> tuple[str, str]:
    """The whole book as email text (takeaways, then each story) so Gmail can search and show it."""
    text = [name, f"{len(summaries)} video(s)", "", "TODAY'S VIDEOS"]
    text += [f"- {s.video['channel']}: {s.video['title']}. {s.takeaway}" for s in summaries]
    html = [f"<h2>{escape(name)}</h2>", overview_html(summaries, "Today's videos").replace("<h1>", "<h3>")
            .replace("</h1>", "</h3>")]
    for s in summaries:
        url = f"https://www.youtube.com/watch?v={s.video_id}"
        text += ["", "=" * 60, s.video["title"], f"{s.video['channel']} · {url}", ""] + [p + "\n" for p in s.paragraphs]
        html.append("<hr>" + chapter_html(s).replace("<h1>", "<h3>").replace("</h1>", "</h3>"))
    text.append("Sent by youtube-kindle")
    html.append('<p style="color:#888">Sent by youtube-kindle</p>')
    return "\n".join(text), "".join(html)


def build_epub(name: str, summaries: list[Summary], slot: datetime, out_dir: Path) -> Path:
    from epub_writer import write_epub
    chapters = [("Today's videos", overview_html(summaries, name))]
    chapters += [(s.video["title"], chapter_html(s)) for s in summaries]
    return write_epub(out_dir / f"{name}.epub", name, chapters, author="YouTube digest", css=rk.CSS,
                      identifier=f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, APP + ':' + name + ':' + slot.isoformat())}")


def overview_words(summaries: list[Summary]) -> int:
    return 10 + sum(len(f"{s.video['channel']} {s.video['title']} {s.takeaway}".split()) for s in summaries)


def book_words(summaries: list[Summary]) -> int:
    return overview_words(summaries) + sum(s.words() for s in summaries)


def make_book(session, cfg: dict, ready: list[tuple[str, dict]], label: str, slot: datetime,
              out_dir: Path) -> tuple[Path, list[Summary]]:
    lengths = plan_lengths(session, cfg, ready)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda item: _safe_summarise(session, cfg, *item, lengths[item[0]]), ready))
    summaries = [s for s in results if s is not None]
    if not summaries:
        raise RuntimeError(f"No summaries could be written ({rk.ai_provider(cfg['shared'])} unavailable?)")
    # Book names use the same AI keyword picker as raindrop-kindle.
    arts = [rk.Article(index=n, bookmark={}, title=s.video["title"], url=f"https://www.youtube.com/watch?v={s.video_id}",
                       body_html=chapter_html(s)) for n, s in enumerate(summaries, 1)]
    rk.assign_keywords(arts, False, session, cfg["shared"])
    for s, a in zip(summaries, arts):
        s.keyword = a.keyword
    name = rk.book_name(label, arts)
    path = build_epub(name, summaries, slot, out_dir)
    total = book_words(summaries)
    log.info("Built %s: %d videos, ~%d words (~%.1f pages), summaries: %s", path.name, len(summaries), total,
             total / WORDS_PER_PAGE, rk.cost_label(rk.total_cost(s.cost for s in summaries)))
    return path, summaries


def _safe_summarise(session, cfg, video_id, video, words) -> Summary | None:
    # The writer occasionally returns malformed JSON; one immediate retry beats delaying the book.
    for attempt in (1, 2):
        try:
            return summarise(session, cfg, video_id, video, words)
        except Exception as e:
            log.warning("Summary of %r failed (attempt %d): %s", video["title"][:60], attempt, e)
    return None


# --------------------------------------------------------------------------
# Batches and commands
# --------------------------------------------------------------------------

def refresh(session, cfg: dict, state: dict, now: datetime, allow_whisper: bool,
            channels: list[dict] | None = None) -> None:
    """Pick up new uploads and get each one a transcript. Only runs when a book is due."""
    poll_feeds(session, channels if channels is not None else load_channels(), state, rk.parse_dt(state["watermark"]))
    state["last_poll"] = now.isoformat()
    save_state(state)
    budget = CloudBudget(float(cfg.get("transcribe", {}).get("cloud_minutes_per_run", 90)))
    for vid, video in sorted(state["videos"].items(), key=lambda kv: kv[1]["published"]):
        if video["status"] not in ("new", "waiting"):
            continue
        classify(vid, video)
        if video["status"] == "new" and (channels is None or video["channel_id"] in {c["id"] for c in channels}):
            prepare_transcript(vid, video, cfg, allow_whisper, now, session, budget)
        save_state(state)


def run_batch(session, cfg: dict, state: dict, slot: datetime, sched: Schedule, mark_slot: bool,
              label: str | None = None, channel_ids: set[str] | None = None) -> None:
    label = label or sched.label(slot)
    ready = [(vid, v) for vid, v in sorted(state["videos"].items(), key=lambda kv: kv[1]["published"])
             if v["status"] == "ready" and transcript_path(vid).exists()
             and (channel_ids is None or v["channel_id"] in channel_ids)]
    waiting = sum(v["status"] in ("new", "waiting") for v in state["videos"].values())
    if not ready:
        log.info("%s: no new videos ready%s", label, f" ({waiting} still waiting for a transcript)" if waiting else "")
    else:
        log.info("%s: summarising %d video(s)", label, len(ready))
        path, summaries = make_book(session, cfg, ready, label, slot, BOOKS_DIR)
        text, html = email_body(path.stem, summaries)
        rk.send_to_kindle(cfg["shared"], path, subject=f"YouTube: {path.stem}", text=text, html=html)
        for s in summaries:
            state["videos"][s.video_id]["status"] = "sent"
            transcript_path(s.video_id).unlink(missing_ok=True)
        state["history"] = (state["history"] + [{
            "slot": slot.isoformat(), "file": path.name, "videos": len(summaries),
            "sent_at": now_local().isoformat()}])[-50:]
        log.info("Sent %s", path.name)
        rk.notify(f"Sent to Kindle: {path.stem}", f"{len(summaries)} video summaries")
    if mark_slot:
        state["last_slot_done"] = max(rk.parse_dt(state["last_slot_done"]), slot).isoformat()
        state["pending_open"] = False
    state["retry_after"] = None
    save_state(state)


def cmd_tick(args) -> int:
    with Lock(wait=False) as locked:
        if not locked:
            return 0
        try:
            cfg = load_config()
        except rk.SetupError as e:
            log.debug("%s", e)
            return 0
        if not load_channels():
            return 0
        sched = Schedule(cfg)
        now = now_local()
        state = load_state(sched, now)
        last = rk.parse_dt(state["last_tick"])
        if last is None or now - last > WAKE_GAP:
            state["pending_open"] = True
            log.info("Boot/wake detected")
        state["last_tick"] = now.isoformat()
        save_state(state)

        session = rk.http()
        retry = rk.parse_dt(state.get("retry_after"))
        slot = None if retry and now < retry else rk.choose_slot(state, sched, now)
        try:
            if slot is not None:
                if not online():
                    log.info("Offline; will retry on the next check")
                    return 0
                if slot > now:
                    log.info("Next slot %s is under %d min away; running it now",
                             sched.label(slot), sched.bring_forward.seconds // 60)
                refresh(session, cfg, state, now, allow_whisper=True)
                run_batch(session, cfg, state, slot, sched, mark_slot=True)
            elif state["pending_open"]:
                state["pending_open"] = False  # opened the laptop, but no book is due
                save_state(state)
        except rk.Offline as e:
            log.info("Offline (%s); will retry on the next check", e)
        except Exception as e:
            log.error("YouTube batch failed: %s", e, exc_info=not isinstance(e, rk.SetupError))
            state["retry_after"] = (now + FAILURE_BACKOFF).isoformat()
            save_state(state)
            rk.notify("YouTube → Kindle failed", str(e), urgent=True)
            return 1
    return 0


def cmd_send_now(args) -> int:
    cfg = load_config()
    sched = Schedule(cfg)
    with Lock(wait=True):
        now = now_local()
        state = load_state(sched, now)
        session = rk.http()
        channels = None
        if args.channel:
            channels = [c for c in load_channels() if args.channel.lower() in c["name"].lower()]
            if not channels:
                print(f"No followed channel matches {args.channel!r}.")
                return 1
        refresh(session, cfg, state, now, allow_whisper=True, channels=channels)
        run_batch(session, cfg, state, sched.due(now), sched, mark_slot=False,
                  label=f"{now:%b%d}_YT_C" if channels else None,  # C = channel catch-up
                  channel_ids={c["id"] for c in channels} if channels else None)
    return 0


def cmd_preview(args) -> int:
    """Build a book from the last N hours of uploads without touching the real state."""
    cfg = load_config()
    sched = Schedule(cfg)
    now = now_local()
    channels = [c for c in load_channels() if not args.channel or args.channel.lower() in c["name"].lower()]
    if not channels:
        print("No channels. Add some with `youtube-kindle add <name or link>`.")
        return 1
    state = {"videos": {}, "watermark": (now - timedelta(hours=args.hours)).isoformat()}
    session = rk.http()
    poll_feeds(session, channels, state, rk.parse_dt(state["watermark"]))
    ready = []
    for vid, video in sorted(state["videos"].items(), key=lambda kv: kv[1]["published"]):
        if video["status"] == "skipped":
            continue  # already logged when the feed was read
        classify(vid, video)
        if video["status"] != "new":
            print(f"  skip ({video.get('reason', video['status'])}): {video['title']}")
            continue
        if prepare_transcript(vid, video, cfg, args.whisper, now + CAPTION_GRACE, session,
                              CloudBudget(60)) in ("ready", "whispered"):
            ready.append((vid, video))
        else:
            print(f"  no captions (use --whisper to transcribe locally): {video['title']}")
    if not ready:
        print("No videos to summarise in that window.")
        return 0
    label = sched.label(sched.due(now))
    path, summaries = make_book(session, cfg, ready, label, sched.due(now), STATE_DIR / "preview")
    total = book_words(summaries)
    print(f"\n{path}\n  {len(summaries)} videos, ~{total} words (~{total / WORDS_PER_PAGE:.1f} Kindle pages), "
          f"summaries: {rk.cost_label(rk.total_cost(s.cost for s in summaries))}")
    for s in summaries:
        print(f"  {s.keyword:<21} {s.video['channel'][:18]:<19} {s.video['title'][:55]}")
    print("Nothing was sent.")
    return 0


def tab_videos(ch: dict, since: datetime, until: datetime, known: set[str]) -> dict[str, dict]:
    """Older regular uploads from a channel's Videos tab (it never lists Shorts)."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "playlistend": 40,
            "logger": _QuietYtdlp()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            listing = ydl.extract_info(f"https://www.youtube.com/channel/{ch['id']}/videos", download=False)
    except Exception as e:
        log.warning("Couldn't list %s's videos: %s", ch["name"], str(e).splitlines()[0][:200])
        return {}
    found = {}
    for entry in listing.get("entries") or []:
        vid = entry.get("id")
        if not vid or vid in known:
            continue
        info = video_details(vid)
        if not info or not info.get("timestamp"):
            continue
        published = datetime.fromtimestamp(info["timestamp"], DISPLAY_TZ)
        if published <= since:
            break  # the tab is newest first
        if published > until:
            continue
        video = {"channel": ch["name"], "channel_id": ch["id"], "title": info.get("title") or entry.get("title", vid),
                 "published": published.isoformat(), "description": (info.get("description") or "")[:1500],
                 "status": "new"}
        apply_details(video, info)
        found[vid] = video
    return found


def collect_videos(session, channels: list[dict], since: datetime, until: datetime) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for ch in channels:
        try:
            entries = feed_entries(session, ch)
        except rk.Offline:
            raise
        except Exception as e:
            log.warning("Feed for %s failed: %s", ch["name"], e)
            entries = []
        found.update({vid: v for vid, published, v in entries if since < published <= until})
        oldest = min((published for _, published, _ in entries), default=None)
        if oldest is None or oldest > since:  # the feed's 15 entries don't reach back far enough
            found.update(tab_videos(ch, since, until, {vid for vid, _, _ in entries}))
        n = sum(v["channel_id"] == ch["id"] and v["status"] != "skipped" for v in found.values())
        log.info("  %-28s %d video(s)", ch["name"], n)
    return found


def cmd_backfill(args) -> int:
    """One book per day for the past N days, ending where the regular service began."""
    cfg = load_config()
    sched = Schedule(cfg)
    with Lock(wait=True):
        now = now_local()
        state = load_state(sched, now)
        until = rk.parse_dt(state["watermark"]).astimezone(DISPLAY_TZ)
        last_day = until.date() - timedelta(days=1)
        first_day = last_day - timedelta(days=args.days - 1)
        since = datetime.combine(first_day, dtime(0), DISPLAY_TZ)
        session = rk.http()
        log.info("Collecting videos from %s to %s", f"{since:%b %d}", f"{until:%b %d %H:%M}")
        found = {vid: v for vid, v in collect_videos(session, load_channels(), since, until).items()
                 if state["videos"].get(vid, {}).get("status") != "sent"}
        probe = next((vid for vid, v in found.items() if v["status"] != "skipped"), None)
        if probe and not args.cloud:
            try:
                fetch_captions(probe)
            except CaptionsBlocked:
                print("YouTube is temporarily blocking caption downloads from this connection, so every video\n"
                      "would need paid cloud transcription (~$0.034 per hour of audio). Try again in a few hours,\n"
                      "or add --cloud to go ahead anyway. Nothing was sent.")
                return 1
            except Exception:
                pass
        budget = CloudBudget(float("inf") if args.cloud else 60)
        days: dict = {}
        for vid, video in sorted(found.items(), key=lambda kv: kv[1]["published"]):
            if video["status"] != "skipped":
                classify(vid, video)
            if video["status"] == "new":
                prepare_transcript(vid, video, cfg, not args.no_whisper, now, session, budget)
            state["videos"][vid] = video
            day = min(datetime.fromisoformat(video["published"]).date(), last_day)  # this morning joins the last day
            days.setdefault(day, []).append((vid, video))
        save_state(state)
        sent = 0
        for day in sorted(days):
            ready = [(vid, v) for vid, v in days[day] if v["status"] == "ready"]
            skipped = len(days[day]) - len(ready)
            if not ready:
                log.info("%s: nothing to summarise", f"{day:%b %d}")
                continue
            log.info("%s: %d video(s)%s", f"{day:%b %d}", len(ready), f", {skipped} skipped" if skipped else "")
            slot = datetime.combine(day, dtime(23, 59), DISPLAY_TZ)
            path, summaries = make_book(session, cfg, ready, f"{day:%b%d}_YT_D", slot,
                                        STATE_DIR / "preview" if args.dry_run else BOOKS_DIR)
            if args.dry_run:
                print(f"  built (not sent): {path.name}")
                continue
            text, html = email_body(path.stem, summaries)
            rk.send_to_kindle(cfg["shared"], path, subject=f"YouTube: {path.stem}", text=text, html=html)
            for sm in summaries:
                state["videos"][sm.video_id]["status"] = "sent"
                transcript_path(sm.video_id).unlink(missing_ok=True)
            state["history"] = (state["history"] + [{"slot": slot.isoformat(), "file": path.name,
                                                     "videos": len(summaries), "sent_at": now_local().isoformat()}])[-50:]
            save_state(state)
            sent += 1
            print(f"  sent: {path.name}  ({len(summaries)} videos)")
        print(f"Done: {sent} book(s) sent." if not args.dry_run else "Dry run: nothing was sent.")
    return 0


def video_id_from(link: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([A-Za-z0-9_-]{11})", link)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", link.strip()):
        return link.strip()
    raise rk.SetupError(f"Not a YouTube video link: {link}")


def lookup_video(session, video_id: str) -> dict:
    """Title, channel, date and type of any video, for one-off commands."""
    o = {}
    try:
        o = session.get("https://www.youtube.com/oembed", timeout=20,
                        params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}).json()
    except Exception:
        pass
    info = video_details(video_id) or {}
    published = datetime.fromtimestamp(info["timestamp"], DISPLAY_TZ) if info.get("timestamp") else now_local()
    video = {"channel": o.get("author_name", "YouTube"), "channel_id": o.get("author_url", ""),
             "title": info.get("title") or o.get("title") or video_id, "published": published.isoformat(),
             "description": (info.get("description") or "")[:1500], "status": "new"}
    if info:
        apply_details(video, info)
    return video


TRANSLATE_PROMPT = """Translate each transcript line into natural, fluent English. Keep names, numbers and
financial terms accurate. Reply with JSON only: {"lines": ["...", "..."]}, exactly one
translated string per input line, in the same order, without timestamps."""


def translate_lines(session, cfg: dict, lines: list[str], per_request: int = 10) -> list[str]:
    """Translate "[mm:ss] text" lines to English, keeping every timestamp. Chunks that come
    back with the wrong number of lines are retried once, then left untranslated."""
    split = [re.match(r"(\[[\d:]+\])\s*(.*)", ln).groups() if re.match(r"\[[\d:]+\]", ln) else ("", ln)
             for ln in lines]
    chunks = [split[i:i + per_request] for i in range(0, len(split), per_request)]

    def one(chunk):
        cost = 0.0
        for _ in (1, 2):
            data = rk.ai_chat(session, cfg["shared"], {
                "model": text_model(cfg),
                "messages": [{"role": "system", "content": TRANSLATE_PROMPT},
                             {"role": "user", "content": json.dumps({"lines": [t for _, t in chunk]}, ensure_ascii=False)}],
                "response_format": {"type": "json_object"},
                "reasoning": {"effort": "minimal"}, "temperature": 0.2, "max_tokens": 6000, "usage": {"include": True},
            })
            cost = rk.total_cost([cost, rk.usage_cost(data)])
            try:
                content = re.sub(r"^```(?:json)?|```$", "", (data["choices"][0]["message"]["content"] or "").strip())
                out = json.loads(content.strip())["lines"]
                if len(out) == len(chunk):
                    return [f"{ts} {str(t).strip()}".strip() for (ts, _), t in zip(chunk, out)], cost, True
            except Exception:
                pass
        return [f"{ts} {t}".strip() for ts, t in chunk], cost, False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, chunks))
    failed = sum(not ok for _, _, ok in results)
    log.info("Translated %d lines (%s)%s", len(lines), rk.cost_label(rk.total_cost(c for _, c, _ in results)),
             f"; {failed} chunk(s) left untranslated" if failed else "")
    return [ln for out, _, _ in results for ln in out]


def cmd_transcript(args) -> int:
    """Save one video's transcript (and an English translation) as text files."""
    global TRANSCRIPTS_DIR
    cfg = load_config()
    session = rk.http()
    vid = video_id_from(args.link)
    video = lookup_video(session, vid)
    TRANSCRIPTS_DIR = STATE_DIR / "samples"  # keep one-off transcripts out of the delivery queue
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    budget = CloudBudget(float(cfg.get("transcribe", {}).get("cloud_max_minutes_per_video", 30)))
    if prepare_transcript(vid, video, cfg, True, now_local() + CAPTION_GRACE, session, budget) not in ("ready", "whispered"):
        print("Couldn't get a transcript for this video right now.")
        return 1
    data = json.loads(transcript_path(vid).read_text())
    text, words = transcript_text(vid)
    slug = re.sub(r"[^a-z0-9]+", "-", video["title"].lower()).strip("-")[:50]
    header = (f"{video['title']} - {video['channel']}\nhttps://www.youtube.com/watch?v={vid}\n"
              f"Published {datetime.fromisoformat(video['published']):%b %d, %Y} - transcript source: "
              f"{data['source']} ({data['language']}) - {words} words\n\n")
    out_dir = Path(args.out_dir).expanduser()
    out = out_dir / f"transcript_{vid}_{slug}.txt"
    out.write_text(header + text + "\n")
    print(f"Transcript: {out}")
    if args.english and not str(data["language"]).startswith("en"):
        english = translate_lines(session, cfg, text.splitlines())
        out_en = out_dir / f"transcript_{vid}_{slug}_english.txt"
        out_en.write_text(header.replace(f"({data['language']})", f"({data['language']}, translated to English)")
                          + "\n".join(english) + "\n")
        print(f"English:    {out_en}")
    return 0


def cmd_send_video(args) -> int:
    """Send one video, of any age, to Kindle as a one-off story chapter."""
    cfg = load_config()
    sched = Schedule(cfg)
    vid = video_id_from(args.link)
    with Lock(wait=True):
        now = now_local()
        state = load_state(sched, now)
        session = rk.http()
        if state["videos"].get(vid, {}).get("status") == "sent" and not args.again:
            print("Already sent to your Kindle (add --again to send it once more).")
            return 1
        video = lookup_video(session, vid)
        if video["status"] == "skipped":
            print(f"Not sending: it's a {video['reason']}.")
            return 1
        budget = CloudBudget(float(cfg.get("transcribe", {}).get("cloud_max_minutes_per_video", 30)))
        if prepare_transcript(vid, video, cfg, True, now + CAPTION_GRACE, session, budget) not in ("ready", "whispered"):
            print("Couldn't get a transcript for this video right now.")
            return 1
        state["videos"][vid] = video
        save_state(state)
        path, summaries = make_book(session, cfg, [(vid, video)], f"{now:%b%d}_YT_C", now, BOOKS_DIR)
        text, html = email_body(path.stem, summaries)
        rk.send_to_kindle(cfg["shared"], path, subject=f"YouTube: {path.stem}", text=text, html=html)
        video["status"] = "sent"
        transcript_path(vid).unlink(missing_ok=True)
        state["history"] = (state["history"] + [{"slot": now.isoformat(), "file": path.name, "videos": 1,
                                                 "sent_at": now_local().isoformat()}])[-50:]
        save_state(state)
        print(f"Sent {path.name}")
    return 0


def cmd_add(args) -> int:
    channels = load_channels()
    session = rk.http()
    for q in args.channels:
        ch = resolve_channel(session, q)
        if any(c["id"] == ch["id"] for c in channels):
            print(f"  already following {ch['name']}")
            continue
        channels.append(ch)
        print(f"  + {ch['name']} {ch['handle']}")
    save_channels(channels)
    return 0


def cmd_remove(args) -> int:
    channels = load_channels()
    keep = [c for c in channels if args.channel.lower() not in (c["name"].lower(), c["id"].lower(), c["handle"].lower())]
    for c in channels:
        if c not in keep:
            print(f"  - {c['name']}")
    if len(keep) == len(channels):
        print(f"No channel matches {args.channel!r}. See `youtube-kindle channels`.")
        return 1
    save_channels(keep)
    return 0


def cmd_channels(args) -> int:
    channels = load_channels()
    if not channels:
        print("No channels yet. Add some with `youtube-kindle add <name or link>`.")
    for c in channels:
        print(f"  {c['name']:<32} {c['handle']:<28} {c['id']}")
    return 0


def cmd_import(args) -> int:
    """Pick channels from a Google Takeout subscriptions.csv."""
    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("Channel Id")]
    following = {c["id"] for c in load_channels()}
    for n, r in enumerate(rows, 1):
        print(f"  {n:>3}. {'✓ ' if r['Channel Id'] in following else '  '}{r.get('Channel Title', '')}")
    picks = input("Numbers to add (e.g. 1,4,7-9), 'all', or Enter for none: ").strip().lower()
    chosen = set()
    if picks == "all":
        chosen = set(range(1, len(rows) + 1))
    else:
        for part in filter(None, (p.strip() for p in picks.split(","))):
            a, _, b = part.partition("-")
            chosen.update(range(int(a), int(b or a) + 1))
    channels = load_channels()
    for n in sorted(chosen):
        r = rows[n - 1]
        if r["Channel Id"] not in following:
            channels.append({"id": r["Channel Id"], "name": r.get("Channel Title", r["Channel Id"]), "handle": ""})
            print(f"  + {r.get('Channel Title')}")
    save_channels(channels)
    return 0


def cmd_setup(args) -> int:
    cfg = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            cfg = tomllib.load(f)
    current = interests(cfg)
    print("YouTube -> Kindle setup. Email, Kindle and AI provider settings come from raindrop-kindle.\n")
    print("What should the summaries focus on? One topic per line; empty line to finish.")
    if current:
        print("Current interests (press Enter right away to keep them):\n  " + current.replace("\n", "\n  "))
    lines = []
    while True:
        line = input("  > ").strip()
        if not line:
            break
        lines.append(line)
    if lines:
        cfg["interests"] = "\n".join(lines)
    write_config(cfg)
    print(f"\nSaved {CONFIG_PATH}. Channels: `youtube-kindle channels`.")
    return 0


def cmd_status(args) -> int:
    cfg = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            cfg = tomllib.load(f)
    sched = Schedule(cfg)
    now = now_local()
    print(f"Channels: {len(load_channels())}   Interests: {'set' if interests(cfg) else 'NOT SET (youtube-kindle setup)'}"
          f"   Each video: {cfg.get('min_pages_per_video', 1)}-{cfg.get('max_pages_per_video', 2)} pages")
    if STATE_PATH.exists():
        state = load_state(sched, now)
        counts = {}
        for v in state["videos"].values():
            counts[v["status"]] = counts.get(v["status"], 0) + 1
        print(f"Last finished slot: {sched.label(rk.parse_dt(state['last_slot_done']))}   Videos: "
              + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
        names = {c["id"]: c["name"] for c in load_channels()}
        for cid, since in state.get("feed_failures", {}).items():
            print(f"  feed problem: {names.get(cid, cid)} since {rk.parse_dt(since):%b %d %H:%M} (will catch up)")
        for h in state["history"][-5:]:
            print(f"  sent {rk.parse_dt(h['sent_at']):%b %d %H:%M}  {h['file']}  ({h['videos']} videos)")
    nxt = sched.next(now)
    print(f"Next slot: {sched.label(nxt)} at {nxt.astimezone(DISPLAY_TZ):%b %d %H:%M} India time")
    sys.stdout.flush()
    subprocess.run(["systemctl", "--user", "list-timers", f"{APP}.timer", "--no-pager"], check=False)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog=APP, description="Send interest-focused summaries of new YouTube videos to Kindle.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="set your interests and book length")
    a = sub.add_parser("add", help="follow channels (name, @handle or link)")
    a.add_argument("channels", nargs="+")
    r = sub.add_parser("remove", help="stop following a channel")
    r.add_argument("channel")
    sub.add_parser("channels", help="list followed channels")
    i = sub.add_parser("import", help="pick channels from a Google Takeout subscriptions.csv")
    i.add_argument("csv")
    sub.add_parser("tick", help="poll, transcribe and send if a batch is due (systemd timer)")
    sn = sub.add_parser("send-now", help="fetch, summarise and send new videos now")
    sn.add_argument("--channel", help="only this channel (name contains), e.g. for a newly added channel's backlog")
    pv = sub.add_parser("preview", help="build a book from recent uploads without sending it")
    pv.add_argument("--hours", type=float, default=24)
    pv.add_argument("--channel", help="only channels whose name contains this")
    pv.add_argument("--whisper", action="store_true", help="transcribe caption-less videos locally")
    b = sub.add_parser("backfill", help="one book per day for the past N days, sent to Kindle")
    b.add_argument("--days", type=int, default=7)
    b.add_argument("--dry-run", action="store_true", help="build the books but don't send them")
    b.add_argument("--no-whisper", action="store_true", help="skip videos without captions")
    b.add_argument("--cloud", action="store_true", help="go ahead even if YouTube blocks captions (paid transcription)")
    tr = sub.add_parser("transcript", help="save one video's transcript as a text file")
    tr.add_argument("link")
    tr.add_argument("--english", action="store_true", help="also save an English translation")
    tr.add_argument("--out-dir", default=".", help="folder for the files (default: current folder)")
    sv = sub.add_parser("send-video", help="send one video, of any age, to Kindle as a story chapter")
    sv.add_argument("link")
    sv.add_argument("--again", action="store_true", help="send even if it was sent before")
    sub.add_parser("status", help="show channels, schedule and recent books")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s" if not sys.stderr.isatty() else "%(message)s")
    for noisy in ("urllib3", "faster_whisper", "httpx", "httpcore", "huggingface_hub", "trafilatura"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
        logging.getLogger(noisy).propagate = False  # huggingface_hub turns httpx logging back on
    commands = {"setup": cmd_setup, "add": cmd_add, "remove": cmd_remove, "channels": cmd_channels,
                "import": cmd_import, "tick": cmd_tick, "send-now": cmd_send_now, "preview": cmd_preview,
                "backfill": cmd_backfill, "transcript": cmd_transcript, "send-video": cmd_send_video,
                "status": cmd_status}
    try:
        return commands[args.cmd](args)
    except rk.SetupError as e:
        print(e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
