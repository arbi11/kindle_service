"""Validate the shipped catalogue, or stage a manifest version for PR review."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from . import load_catalogue, validate_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path(__file__).resolve().parent.parent / "connections")
    parser.add_argument("--pin", metavar="CONNECTION_ID", help="Update the index hash for a manifest being submitted for review")
    args = parser.parse_args()
    folder = args.folder.resolve()
    try:
        if args.pin:
            if not re.fullmatch(r"[a-z][a-z0-9-]{1,49}", args.pin):
                raise ValueError("Use the manifest's connection ID")
            raw = (folder / args.pin / "connection.json").read_bytes()
            manifest = validate_manifest(json.loads(raw))
            if manifest["id"] != args.pin:
                raise ValueError("The folder name must match the connection ID")
            index = json.loads((folder / "catalogue.json").read_text())
            entry = {"id": args.pin, "manifest": args.pin + "/connection.json", "sha256": hashlib.sha256(raw).hexdigest()}
            existing = next((i for i, item in enumerate(index["connections"]) if item["id"] == args.pin), None)
            if existing is None:
                index["connections"].append(entry)
            else:
                index["connections"][existing] = entry
            (folder / "catalogue.json").write_text(json.dumps(index, indent=2) + "\n")
        catalogue = load_catalogue(folder)
        manifests = {path.parent.name for path in folder.glob("*/connection.json")}
        if manifests != set(catalogue):
            raise ValueError("Every submitted connection manifest must be pinned in catalogue.json")
        for connection in catalogue.values():
            print(f"OK {connection['id']} {connection['version']} (connection API {connection['api_version']})")
        print(f"Validated {len(catalogue)} connection versions. Publication still requires maintainer review.")
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.exit(1, "Connection validation failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
