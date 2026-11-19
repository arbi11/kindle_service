#!/usr/bin/env python3
"""Test one saved SRT with OpenAI. Writes only local preview files; never sends mail."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

import requests

import ai_client as ai
import raindrop_kindle as rk
import youtube_kindle as yt


ROOT = Path(__file__).resolve().parent


def read_srt(path: Path) -> list:
    lines = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()):
        match = re.search(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->[^\n]*\n(.+)", block, re.S)
        if match:
            hour, minute, second, ms, text = match.groups()
            start = int(hour) * 3600 + int(minute) * 60 + int(second) + int(ms) / 1000
            lines.append([start, re.sub(r"<[^>]*>", "", text).replace("\n", " ").strip()])
    if not lines:
        raise ValueError(f"No subtitle lines found in {path}")
    return lines


class OpenAISession(requests.Session):
    """Restrict the test to OpenAI and record usage without recording credentials."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def request(self, method, url, **kwargs):
        if urlparse(url).netloc != "api.openai.com" or method.upper() != "POST":
            raise RuntimeError(f"Preview attempted an unexpected endpoint: {url}")
        kwargs["allow_redirects"] = False
        response = super().request(method, url, **kwargs)
        record = {"endpoint": url, "status": response.status_code,
                  "request_id": response.headers.get("x-request-id"),
                  "requested_model": (kwargs.get("json") or kwargs.get("data") or {}).get("model")}
        if response.ok:
            data = response.json()
            record.update(model=data.get("model"), usage=data.get("usage", {}))
        self.calls.append(record)
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, help="override the key in the local shared configuration")
    parser.add_argument("--transcript", type=Path, required=True, help="a saved SRT file to summarise")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "openai-preview")
    parser.add_argument("--words", type=int, default=390)
    args = parser.parse_args()
    if args.key_file and (not args.key_file.is_file() or not args.key_file.read_text().strip()):
        parser.error(f"Save your credited organization's OpenAI key in {args.key_file} first")
    if args.key_file:
        args.key_file.chmod(0o600)
    args.output.mkdir(parents=True, exist_ok=True)
    args.output.chmod(0o700)
    lines = read_srt(args.transcript)
    shared = rk.load_config(need_email=False)
    shared["ai"] = {**shared.get("ai", {}), "provider": "openai",
                    "openai_model": ai.DEFAULT_OPENAI_MODEL, "openai_candidate_writer": ai.DEFAULT_OPENAI_MODEL,
                    "openai_decision_model": ai.DEFAULT_OPENAI_MODEL}
    if args.key_file:
        shared["ai"]["openai_api_key_file"] = str(args.key_file.resolve())
        shared["ai"].pop("openai_api_key", None)
    if not ai.has_key(shared):
        parser.error("Configure an OpenAI API key or pass --key-file")
    with yt.CONFIG_PATH.open("rb") as source:
        cfg = tomllib.load(source)
    cfg["shared"] = shared
    cfg["openai_summary_model"] = ai.DEFAULT_OPENAI_MODEL
    cfg["openai_editor_model"] = ai.DEFAULT_OPENAI_MODEL
    cfg["min_pages_per_video"] = cfg["max_pages_per_video"] = args.words / yt.WORDS_PER_PAGE
    transcript_dir = args.output / "transcripts"
    transcript_dir.mkdir(exist_ok=True)
    video_id = "local-preview"
    (transcript_dir / f"{video_id}.json").write_text(json.dumps({"lines": lines, "language": "en", "source": "saved-srt"}))
    title = re.sub(r"^\[[^\]]*\]\s*", "", args.transcript.stem)
    video = {"title": title, "channel": "Saved transcript", "published": ""}

    def no_delivery(*unused, **kwargs):
        raise RuntimeError("Kindle delivery is disabled in the OpenAI preview")

    with OpenAISession() as session, patch.object(rk, "send_to_kindle", no_delivery), patch.object(yt, "TRANSCRIPTS_DIR", transcript_dir):
        lengths = yt.plan_lengths(session, cfg, [(video_id, video)])
        summary = yt.summarise(session, cfg, video_id, video, lengths[video_id])
        article = rk.Article(1, {}, title, "https://www.youtube.com/", "<p>" + summary.takeaway + "</p>")
        picker = rk.assign_keywords([article], False, session, shared)
        if picker != "OpenAI":
            raise RuntimeError("OpenAI keyword selection failed; the live test is incomplete")
        if any(call["status"] >= 400 for call in session.calls):
            raise RuntimeError("An OpenAI call failed; inspect usage and retry before switching services")
        report = {"provider": "openai", "source": str(args.transcript), "kindle_delivery": False,
                  "live_service_configs_modified": False, "keyword": article.keyword,
                  "takeaway": summary.takeaway, "paragraphs": summary.paragraphs,
                  "summary_words": summary.words(), "target_words": lengths[video_id],
                  "requests": session.calls, "tested_at": datetime.now(timezone.utc).isoformat()}
    text = title + "\n\n" + summary.takeaway + "\n\n" + "\n\n".join(summary.paragraphs) + "\n"
    (args.output / "summary.txt").write_text(text)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"OpenAI preview saved to {args.output}")
    print(f"{len(report['requests'])} OpenAI requests; {summary.words()} summary words; keyword: {article.keyword}")
    print("No Kindle delivery or live service changes. Confirm usage in platform.openai.com/usage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
