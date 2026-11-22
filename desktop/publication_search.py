"""Bounded public-copy discovery; no reader credentials enter this worker."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .feeds import public_url

MAX_RESULTS = 5
QUERY_TIMEOUT = 12


def checked_query(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 1200 or re.search(r"[\x00-\x1f\x7f]", query):
        raise ValueError("Invalid public article search")
    return query.strip()


def checked_results(records):
    if not isinstance(records, list) or len(records) > MAX_RESULTS:
        raise ValueError("Public search returned an invalid result list")
    result, seen = [], set()
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            url = public_url(record.get("url") or record.get("href") or "")
        except ValueError:
            continue
        title, description = record.get("title", ""), record.get("description", record.get("body", ""))
        if not isinstance(title, str) or not isinstance(description, str) or url in seen:
            continue
        seen.add(url)
        result.append({"url": url, "title": title[:1000], "description": description[:700]})
    return result


class PublicSearch:
    def search(self, query, *, timeout=QUERY_TIMEOUT):
        query = checked_query(query)
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve().parent.parent / "daily_edition.py"))
        command.append("--publication-search")
        environment = {name: os.environ[name] for name in (
            "PATH", "LANG", "LC_ALL", "LC_CTYPE", "LD_LIBRARY_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "SYSTEMROOT", "WINDIR") if name in os.environ}
        try:
            process = subprocess.run(command, input=json.dumps({"query": query}), text=True,
                                     capture_output=True, timeout=min(timeout, QUERY_TIMEOUT), check=False,
                                     env=environment)
            if len(process.stdout) > 32000:
                raise ValueError("Public search returned too much data")
            data = json.loads(process.stdout)
            if process.returncode or not isinstance(data, dict) or data.get("error"):
                raise ValueError("Public web search is unavailable; try again later")
            return checked_results(data.get("results"))
        except subprocess.TimeoutExpired:
            raise ValueError("Public web search timed out") from None
        except (OSError, json.JSONDecodeError):
            raise ValueError("Public web search could not start in this build") from None


def run_worker():
    """One short query on stdin; structured results on stdout, before Qt starts."""
    try:
        payload = sys.stdin.read(5000)
        if len(payload) > 4000:
            raise ValueError("Invalid public article search")
        data = json.loads(payload)
        query = checked_query(data["query"])
        from ddgs import DDGS
        results = DDGS(timeout=4).text(query, max_results=MAX_RESULTS, backend="yahoo,mojeek,startpage")
        print(json.dumps({"results": checked_results(results[:MAX_RESULTS])}), flush=True)
        return 0
    except Exception:
        # Engine exceptions can contain internal request details. The UI needs a
        # stable failure, not an engine URL or a search-provider response dump.
        print(json.dumps({"error": "Public web search is unavailable"}), flush=True)
        return 1
