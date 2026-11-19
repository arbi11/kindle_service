"""Keep credentials out of the API, settings database, logs and exports."""
import json
from pathlib import Path
import threading

from .store import private_write

NAMES = ("openai_key", "openrouter_key", "raindrop_token", "smtp_password")


class Secrets:
    def __init__(self, root: Path, *, use_keyring=True):
        self.path = root / "credentials.json"
        self.index_path = root / "keyring-names.json"
        self.lock = threading.RLock()
        self.cache = {}
        self.keyring = None
        self.service = "daily-edition:" + str(root.resolve())
        if use_keyring:
            try:
                import keyring
                backend = keyring.get_keyring()
                if backend.priority > 0 and "fail" not in type(backend).__module__:
                    self.keyring = keyring
            except Exception:
                pass

    def local(self):
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {}

    def indexed(self):
        return json.loads(self.index_path.read_text()) if self.index_path.exists() else []

    def get(self, name):
        if name not in NAMES:
            return ""
        with self.lock:
            if name in self.cache:
                return self.cache[name]
            # A fallback file is the most recent saved value. Do not retrieve an
            # older keyring entry if a credential changed while it was unavailable.
            local = self.local()
            if name in local:
                self.cache[name] = local[name]
                return local[name]
            # Avoid unlocking an empty OS wallet merely to open a fresh app.
            if self.keyring and name in self.indexed():
                try:
                    value = self.keyring.get_password(self.service, name)
                    if value:
                        self.cache[name] = value
                        return value
                except Exception:
                    self.keyring = None
            value = ""
            self.cache[name] = value
            return value

    def set(self, name, value):
        if name not in NAMES:
            raise ValueError("Unknown credential")
        value = str(value).strip()
        with self.lock:
            values = self.local()
            indexed = set(self.indexed())
            if self.keyring:
                try:
                    if value:
                        self.keyring.set_password(self.service, name, value)
                    else:
                        try:
                            self.keyring.delete_password(self.service, name)
                        except self.keyring.errors.PasswordDeleteError:
                            pass
                    values.pop(name, None)
                    indexed.add(name) if value else indexed.discard(name)
                    private_write(self.index_path, json.dumps(sorted(indexed)))
                    if self.path.exists():
                        private_write(self.path, json.dumps(values))
                    self.cache[name] = value
                    return
                except Exception:
                    self.keyring = None
            if value:
                values[name] = value
            else:
                values.pop(name, None)
            private_write(self.path, json.dumps(values))
            indexed.discard(name)
            private_write(self.index_path, json.dumps(sorted(indexed)))
            self.cache[name] = value

    def status(self):
        flags = {name: bool(self.get(name)) for name in NAMES}
        local = bool(self.local())
        storage = "system credential store" if self.keyring and not local else "private local file (owner access only)"
        if self.keyring and local:
            storage = "system credential store and private local file"
        return {"configured": flags, "storage": storage}

    def redact(self, text):
        text = str(text)
        for name in NAMES:
            value = self.get(name)
            if value:
                text = text.replace(value, "[credential hidden]")
        import re
        text = re.sub(r"sk-[A-Za-z0-9_-]{10,}", "[credential hidden]", text)
        return text[:800]
