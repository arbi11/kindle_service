from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets as random_secrets
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from . import __version__
from .content import render_newsletter
from .demo import seed
from .engine import Engine
from .secrets import Secrets, NAMES
from .store import DEFAULTS, Store, now, private_write, uid

ASSETS = Path(__file__).resolve().parent / "ui"


def validate_settings(values):
    result = {}
    for key, value in values.items():
        if key not in DEFAULTS or key.startswith("last_schedule_"):
            continue
        default = DEFAULTS[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be on or off")
        elif isinstance(default, int):
            if type(value) is not int:
                raise ValueError(f"{key} must be a whole number")
        elif isinstance(default, str):
            if not isinstance(value, str) or len(value) > 6000:
                raise ValueError(f"Invalid value for {key}")
            value = value.strip()
        if key == "provider" and value not in ("openai", "openrouter"):
            raise ValueError("Choose OpenAI or OpenRouter")
        if key == "model" and (not value or len(value) > 200 or "\n" in value):
            raise ValueError("Enter a valid model ID")
        bounds = {"summary_words": (150, 2000), "reading_minutes": (5, 180), "lookback_days": (1, 30),
                  "max_items_per_run": (1, 20), "audio_minutes_per_video": (1, 60), "smtp_port": (1, 65535), "reader_size": (16, 28)}
        if key in bounds and not bounds[key][0] <= value <= bounds[key][1]:
            raise ValueError(f"{key.replace('_', ' ')} must be between {bounds[key][0]} and {bounds[key][1]}")
        if key == "timezone":
            try:
                ZoneInfo(value)
            except Exception:
                raise ValueError("Use a timezone such as Asia/Kolkata or Europe/London")
        if key == "reader_theme" and value not in ("light", "sepia", "dark"):
            raise ValueError("Choose a reader theme")
        if key == "delivery_times":
            if not isinstance(value, list) or not 1 <= len(value) <= 4 or any(not isinstance(t, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t) for t in value):
                raise ValueError("Choose one to four delivery times in HH:MM format")
            value = sorted(set(value))
        if key in ("email_to", "kindle_to", "smtp_sender", "smtp_username") and value:
            addresses = re.split(r"[,;\n]", value) if key.endswith("_to") else [value]
            for address in addresses:
                if not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", address.strip()):
                    raise ValueError("Enter a valid email address")
            value = ", ".join(a.strip() for a in addresses)
        if key == "smtp_host" and ("/" in value or re.search(r"\s", value) or len(value) > 253):
            raise ValueError("Enter the mail server hostname, such as smtp.gmail.com")
        result[key] = value
    return result


class Application:
    def __init__(self, root, *, port=0, use_keyring=True, scheduler=True, seed_samples=True):
        self.store = Store(Path(root))
        self.secrets = Secrets(self.store.root, use_keyring=use_keyring)
        if seed_samples:
            seed(self.store)
        self.engine = Engine(self.store, self.secrets)
        self.token = random_secrets.token_urlsafe(32)
        self.http = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.http.daemon_threads = True
        self.http.app = self
        self.port = self.http.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.url = self.origin + "/?session=" + self.token
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True, name="local-reader")
        self.thread.start()
        private_write(self.store.root / "runtime.json", json.dumps({"pid": os.getpid(), "port": self.port, "token": self.token}))
        if scheduler:
            threading.Thread(target=self.engine.scheduler, daemon=True, name="edition-scheduler").start()

    def bootstrap(self):
        settings = self.store.settings()
        sources, editions = self.store.sources(), self.store.editions()
        stats = self.store.query("""SELECT COUNT(*) AS total,COALESCE(SUM(is_read=0),0) AS unread,
             COALESCE(SUM(starred),0) AS saved,COALESCE(SUM(words),0) AS words FROM items
             WHERE is_sample=CASE WHEN EXISTS(SELECT 1 FROM items WHERE is_sample=0) THEN 0 ELSE 1 END""", one=True)
        zone = ZoneInfo(settings["timezone"])
        local = datetime.now(zone)
        slots = []
        for offset in (0, 1):
            day = local.date() + timedelta(days=offset)
            for t in settings["delivery_times"]:
                h, m = map(int, t.split(":"))
                slot = datetime(day.year, day.month, day.day, h, m, tzinfo=zone)
                if slot > local:
                    slots.append(slot)
        legacy = (Path.home() / ".config/raindrop-kindle/config.toml").exists()
        return {"version": __version__, "settings": settings, "secrets": self.secrets.status(),
                "catalogue": self.engine.catalogue.public(sources),
                "sources": sources, "editions": editions, "stats": stats,
                "jobs": self.store.query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 8"),
                "deliveries": self.store.query("SELECT d.*,e.title AS edition_title FROM deliveries d LEFT JOIN editions e ON e.id=d.edition_id ORDER BY d.created_at DESC LIMIT 30"),
                "next_run": min(slots).isoformat() if slots and settings["schedule_enabled"] else None,
                "legacy_available": legacy, "data_directory": str(self.store.root),
                "autostart": self.autostart_path().exists()}

    def autostart_path(self):
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart/daily-edition.desktop"

    def set_autostart(self, enabled):
        path = self.autostart_path()
        if not enabled:
            path.unlink(missing_ok=True)
            return
        if not sys.platform.startswith("linux"):
            raise ValueError("Launch at login is currently available in the Linux desktop build")
        args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve().parent.parent / "daily_edition.py")]
        args += ["--background", "--data-dir", str(self.store.root)]
        command = " ".join('"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"' for a in args)
        private_write(path, f"[Desktop Entry]\nType=Application\nName=Daily Edition\nExec={command}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")

    def close(self):
        self.engine.close()
        self.http.shutdown()
        self.http.server_close()
        runtime = self.store.root / "runtime.json"
        try:
            if runtime.exists() and json.loads(runtime.read_text()).get("pid") == os.getpid():
                runtime.unlink()
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = "DailyEdition"

    def log_message(self, *_):
        pass  # Never write session URLs, source text or credentials into request logs.

    @property
    def app(self):
        return self.server.app

    def headers_common(self, content_type, length, extra=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        if status == 200:
            self.headers_common("application/json; charset=utf-8", len(payload))
        else:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        self.wfile.write(payload)

    def authorized(self):
        if self.headers.get("Host") != f"127.0.0.1:{self.app.port}":
            return False
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return False
        cookie = jar.get("edition_session")
        return bool(cookie and random_secrets.compare_digest(cookie.value, self.app.token))

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/" and "session" in params and self.headers.get("Host") == f"127.0.0.1:{self.app.port}":
            token = params["session"][0]
            if random_secrets.compare_digest(token, self.app.token):
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"edition_session={self.app.token}; HttpOnly; SameSite=Strict; Path=/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                return
        if not self.authorized():
            self.json({"error": "Open Daily Edition from its desktop launcher to start a private session."}, 401)
            return
        try:
            path = parsed.path
            if path == "/api/bootstrap":
                self.json(self.app.bootstrap())
            elif path == "/api/connections":
                self.json({"connections": self.app.engine.catalogue.public(self.app.store.sources())})
            elif path == "/api/items":
                self.json({"items": self.app.store.items(params.get("q", [""])[0], params.get("kind", ["all"])[0], params.get("status", ["all"])[0])})
            elif re.fullmatch(r"/api/items/[\w-]+", path):
                item = self.app.store.query("SELECT * FROM items WHERE id=?", (path.rsplit("/", 1)[1],), one=True)
                if not item:
                    raise ValueError("This piece could not be found")
                self.json(item)
            elif re.fullmatch(r"/api/editions/[\w-]+(?:/(?:epub|html))?", path):
                parts = path.split("/")
                edition = self.app.store.edition(parts[3])
                if len(parts) == 4:
                    self.json(edition)
                elif parts[4] == "epub":
                    payload = self.app.engine.export_epub(parts[3]).read_bytes()
                    self.headers_common("application/epub+zip", len(payload), {"Content-Disposition": 'attachment; filename="Daily-Edition.epub"'})
                    self.wfile.write(payload)
                else:
                    payload = render_newsletter(edition).encode()
                    self.headers_common("text/html; charset=utf-8", len(payload), {"Content-Disposition": 'attachment; filename="Daily-Edition.html"'})
                    self.wfile.write(payload)
            else:
                assets = {"/": "index.html", "/app.css": "app.css", "/app.js": "app.js", "/icon.svg": "icon.svg"}
                if path not in assets:
                    self.json({"error": "Not found"}, 404)
                    return
                file = ASSETS / assets[path]
                payload = file.read_bytes()
                media = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                self.headers_common(media + ("; charset=utf-8" if media.startswith("text/") else ""), len(payload))
                self.wfile.write(payload)
        except (ValueError, KeyError) as exc:
            self.json({"error": self.app.secrets.redact(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.json({"error": self.app.secrets.redact(exc)}, 500)

    def do_POST(self):
        self.mutate("POST")

    def do_PATCH(self):
        self.mutate("PATCH")

    def do_DELETE(self):
        self.mutate("DELETE")

    def mutate(self, method):
        if not self.authorized() or self.headers.get("Origin", self.app.origin) != self.app.origin or self.headers.get("X-Daily-Edition") != "1":
            self.json({"error": "This request did not come from your app session."}, 403)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.json({"error": "Use a JSON request"}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > 36 * 1024 * 1024:
                raise ValueError("The request is too large")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Invalid request")
            path = urlparse(self.path).path
            store = self.app.store
            if path == "/api/settings" and method == "POST":
                settings = validate_settings(data.get("settings", {}))
                merged = {**store.settings(), **settings}
                if merged["auto_send"] and not (merged["email_enabled"] and merged["email_to"] or merged["kindle_enabled"] and merged["kindle_to"]):
                    raise ValueError("Choose at least one delivery address before enabling automatic sending")
                credentials = data.get("credentials", {})
                if not isinstance(credentials, dict) or any(k not in NAMES or not isinstance(v, str) or len(v) > 6000 for k, v in credentials.items()):
                    raise ValueError("Invalid credentials")
                for key, value in credentials.items():
                    self.app.secrets.set(key, value)
                if settings.get("schedule_enabled") and not store.settings()["schedule_enabled"]:
                    zone = ZoneInfo(merged["timezone"])
                    current = datetime.now(zone)
                    # Enabling starts with the next scheduled slot; catch-up applies after that.
                    settings["last_schedule_slot"] = current.isoformat()
                store.save_settings(settings)
                self.json({"message": "Settings saved", "settings": store.settings(), "secrets": self.app.secrets.status()})
            elif path == "/api/autostart" and method == "POST":
                if type(data.get("enabled")) is not bool:
                    raise ValueError("Choose on or off")
                self.app.set_autostart(data["enabled"])
                self.json({"message": "Launch at login updated"})
            elif path == "/api/jobs" and method == "POST":
                if data.get("kind") == "import_epub":
                    raise ValueError("Use the EPUB file picker to import an edition")
                job_id = self.app.engine.submit(data.get("kind"), data.get("payload", {}))
                self.json({"job_id": job_id})
            elif path == "/api/sources" and method == "POST":
                kind = data.get("kind")
                connection = self.app.engine.catalogue.get(kind)
                if kind == "youtube":
                    self.json({"job_id": self.app.engine.submit("add_youtube", data)})
                elif kind == "raindrop":
                    if not self.app.secrets.get("raindrop_token"):
                        raise ValueError("Connect your Raindrop account first")
                    cid = int(data.get("collection_id", 0))
                    if cid < -1:
                        raise ValueError("Invalid collection ID")
                    tags = [str(t).strip()[:100] for t in data.get("tags", [])][:20]
                    title = str(data.get("title", "Saved articles"))[:200]
                    source_id = store.add_source(kind, title, str(cid), {"collection_id": cid, "tags": tags})
                    self.json({"id": source_id, "message": "Raindrop collection added"})
                elif kind == "article":
                    url = str(data.get("url", "")).strip()
                    parsed = urlparse(url)
                    if parsed.scheme not in ("http", "https") or not parsed.hostname or len(url) > 2000:
                        raise ValueError("Enter a full article URL starting with https://")
                    self.json({"id": store.add_source(kind, parsed.hostname.removeprefix("www."), url), "message": "Article added. Prepare an edition to read it."})
                elif connection.definition["adapter"] == "rss":
                    definition = connection.definition
                    source_id = store.add_source(kind, definition["name"] + " · Recently Unlocked" if kind == "freedium" else definition["name"],
                                                 definition["feed_url"], {"connection_version": definition["version"]})
                    self.json({"id": source_id, "message": definition["name"] + " connected"})
                else:
                    raise ValueError("Choose a source type")
            elif re.fullmatch(r"/api/sources/[\w-]+", path):
                source_id = path.rsplit("/", 1)[1]
                if method == "DELETE":
                    store.execute("DELETE FROM sources WHERE id=?", (source_id,))
                elif method == "PATCH" and isinstance(data.get("enabled"), bool):
                    store.execute("UPDATE sources SET enabled=? WHERE id=?", (int(data["enabled"]), source_id))
                else:
                    raise ValueError("Invalid source update")
                self.json({"message": "Source updated"})
            elif re.fullmatch(r"/api/items/[\w-]+", path) and method == "PATCH":
                item_id = path.rsplit("/", 1)[1]
                for key in ("is_read", "starred"):
                    if key in data:
                        if type(data[key]) is not bool:
                            raise ValueError("Choose on or off")
                        store.execute(f"UPDATE items SET {key}=? WHERE id=?", (int(data[key]), item_id))
                self.json({"message": "Reading updated"})
            elif re.fullmatch(r"/api/deliveries/[\w-]+/resolve", path) and method == "POST":
                delivery_id = path.split("/")[3]
                resolution = data.get("resolution")
                if resolution not in ("sent", "retry"):
                    raise ValueError("Confirm receipt or explicitly allow a retry")
                store.execute("UPDATE deliveries SET status=?,message=? WHERE id=? AND status='uncertain'",
                              ("sent" if resolution == "sent" else "failed",
                               "Receipt confirmed by you" if resolution == "sent" else "Retry explicitly allowed by you", delivery_id))
                self.json({"message": "Delivery updated"})
            elif path == "/api/import-epub" and method == "POST":
                import base64
                try:
                    payload = base64.b64decode(data.get("content", ""), validate=True)
                except Exception:
                    raise ValueError("Choose a valid EPUB file")
                if len(payload) > 25 * 1024 * 1024:
                    raise ValueError("Choose an EPUB smaller than 25 MB")
                path = store.root / "imports" / (uid() + ".epub")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                try:
                    job_id = self.app.engine.submit("import_epub", {"path": str(path)})
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
                self.json({"job_id": job_id})
            else:
                self.json({"error": "Not found"}, 404)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.json({"error": self.app.secrets.redact(exc)}, 400)
        except Exception as exc:
            self.json({"error": self.app.secrets.redact(exc)}, 500)
