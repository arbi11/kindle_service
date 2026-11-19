#!/usr/bin/env python3
"""One-time offline reading import. Close the desktop app before applying it."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3

from desktop.archive_import import import_archive, read_archive
from desktop.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="The desktop reader's data directory")
    parser.add_argument("--state-root", type=Path, default=Path.home() / ".local/state")
    parser.add_argument("--dry-run", action="store_true", help="Audit the archive without modifying the reader")
    args = parser.parse_args()
    editions, skipped = read_archive(args.state_root.expanduser())
    unique = {(story.connection, story.external_id) for edition in editions for story in edition.stories}
    report = {"retained_editions": len(editions), "unique_stories": len(unique),
              "stories_by_connection": dict(Counter(connection for connection, _ in unique)),
              "without_retained_delivery_record": sum(not edition.delivery_recorded for edition in editions),
              "unsent_files_excluded": len(skipped),
              "chapters_without_saved_text": sum(story.summary_missing for edition in editions for story in edition.stories)}
    if args.dry_run:
        print(json.dumps(report))
        return
    if not editions:
        raise SystemExit("No retained reading editions were found")
    root = args.data_dir.expanduser().resolve()
    if not (root / "library.sqlite3").is_file():
        raise SystemExit("Choose an existing desktop reader library")
    with (root / "instance.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Close the desktop reader before importing its archive")
        backups = root / "backups"
        backups.mkdir(mode=0o700, exist_ok=True)
        backup = backups / ("before-reading-import-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".sqlite3")
        with sqlite3.connect(root / "library.sqlite3") as source, sqlite3.connect(backup) as target:
            source.backup(target)
        backup.chmod(0o600)
        store = Store(root)
        settings_before = store.settings()
        sources_before = store.sources()
        report.update(import_archive(store, editions))
        assert store.settings() == settings_before
        assert store.sources() == sources_before
        report["settings_and_connections_preserved"] = True
        print(json.dumps(report))


if __name__ == "__main__":
    main()
