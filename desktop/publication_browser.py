"""A small local browser worker sharing only a publication's own saved profile.

The reader's authenticated browser profile is never used for publisher pages.
Requests/results are private, short-lived files, not browser debugging ports.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .feeds import public_url
from .store import private_write, uid


class PublicationBrowser:
    def __init__(self, root):
        self.root = Path(root)

    def request(self, source_id, url, *, login=False):
        if not re.fullmatch(r"[a-f0-9]{32}", source_id):
            raise ValueError("Choose a saved publication")
        url = public_url(url)
        folder = self.root / "publication-browser/jobs"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder.chmod(0o700)
        request = folder / (uid() + ".json")
        result = request.with_suffix(".result.json")
        private_write(request, json.dumps({"source_id": source_id, "url": url, "login": login}))
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve().parent.parent / "daily_edition.py"))
        command += ["--data-dir", str(self.root), "--publication-browser-job", str(request)]
        try:
            subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=620 if login else 65, check=False)
            if not result.exists() or result.stat().st_size > 12 * 1024 * 1024:
                raise ValueError("The publication browser could not finish. Open the desktop app and try again.")
            data = json.loads(result.read_text())
            if not data.get("ok"):
                raise ValueError(data.get("error") or "The publication browser could not finish")
            return data
        except subprocess.TimeoutExpired:
            raise ValueError("Sign-in timed out. Try again and finish in the browser window." if login
                             else "The publication took too long to load. Try signing in again.") from None
        finally:
            request.unlink(missing_ok=True)
            result.unlink(missing_ok=True)


def run_worker(root, request_path):
    """Run before the reader's instance lock, in this process's Qt main thread."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication, QMainWindow, QToolBar, QLineEdit, QPushButton, QLabel
    from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
                                        QWebEngineUrlRequestInterceptor)
    from PySide6.QtWebEngineWidgets import QWebEngineView

    os.umask(0o077)
    root, request_path = Path(root).resolve(), Path(request_path).resolve()
    if request_path.parent != root / "publication-browser/jobs" or request_path.stat().st_size > 6000:
        raise ValueError("Invalid publication browser request")
    request = json.loads(request_path.read_text())
    source_id, address = request["source_id"], public_url(request["url"])
    if not re.fullmatch(r"[a-f0-9]{32}", source_id):
        raise ValueError("Invalid publication profile")
    login = request.get("login") is True
    folder = root / "publication-browser/profiles" / source_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Quib27 publication browser")
    app.setQuitOnLastWindowClosed(False)
    output = {"ok": False, "error": "Sign-in was cancelled." if login else "The page could not be read."}

    class PublicRequests(QWebEngineUrlRequestInterceptor):
        checked = {}

        def interceptRequest(self, info):
            target = info.requestUrl()
            if target.scheme() in ("data", "blob", "about"):
                return
            host = target.host().lower().rstrip(".")
            if target.scheme() != "https" or target.port(443) != 443 or not host:
                info.block(True)
                return
            if host not in self.checked:
                try:
                    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                    self.checked[host] = bool(addresses) and all(ipaddress.ip_address(x[4][0]).is_global for x in addresses)
                except (OSError, ValueError):
                    self.checked[host] = False
            if not self.checked[host]:
                info.block(True)

    def allowed_navigation(target):
        if target.toString() == "about:blank":
            return True
        try:
            public_url(target.toString())
            # Only interactive sign-in may visit an SSO provider. Automatic
            # captures stay on the selected publisher and its subdomains.
            host, selected = target.host().lower(), urlsplit(address).hostname.removeprefix("www.")
            return login or host == selected or host.endswith("." + selected)
        except ValueError:
            return False

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, target, kind, main_frame):
            return allowed_navigation(target) if main_frame else target.scheme() in ("https", "about", "data")

        def javaScriptConsoleMessage(self, *_):
            pass  # Publisher console messages can contain account details.

    class Window(QMainWindow):
        def closeEvent(self, event):
            app.quit()
            event.accept()

    window = Window()
    window.setWindowTitle("Quib27 · Sign in to " + urlsplit(address).hostname)
    window.resize(1120, 820)
    profile = QWebEngineProfile("publication-" + source_id, window)
    profile.setPersistentStoragePath(str(folder / "storage"))
    profile.setCachePath(str(folder / "cache"))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    interceptor = PublicRequests(profile)
    profile.setUrlRequestInterceptor(interceptor)
    profile.downloadRequested.connect(lambda download: download.cancel())
    page = Page(profile, window)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
    page.newWindowRequested.connect(lambda new: page.setUrl(new.requestedUrl()) if login and allowed_navigation(new.requestedUrl()) else None)
    view = QWebEngineView(window)
    view.setPage(page)
    window.setCentralWidget(view)

    def finish(data):
        nonlocal output
        output = data
        app.quit()

    if login:
        toolbar = QToolBar(window)
        toolbar.setMovable(False)
        window.addToolBar(toolbar)
        entry = QLineEdit(address)
        entry.setMinimumWidth(360)
        toolbar.addWidget(entry)
        def navigate():
            try:
                page.setUrl(QUrl(public_url(entry.text())))
            except ValueError:
                entry.setText(page.url().toString())
        entry.returnPressed.connect(navigate)
        page.urlChanged.connect(lambda url: entry.setText(url.toString()))
        reload = QPushButton("Reload")
        reload.clicked.connect(view.reload)
        toolbar.addWidget(reload)
        done = QPushButton("Finish sign-in")
        done.clicked.connect(lambda: finish({"ok": True}))
        toolbar.addWidget(done)
        window.statusBar().addWidget(QLabel("Sign in on the publisher's website, then click Finish sign-in. This session stays on this laptop."))
        window.show()
    else:
        def capture():
            def received(body):
                if len(body.encode("utf-8")) > 8 * 1024 * 1024:
                    finish({"ok": False, "error": "This publication page is too large."})
                else:
                    finish({"ok": True, "html": body, "url": page.url().toString()})
            page.toHtml(received)
        # Let client-rendered content and subscriber access finish loading.
        page.loadFinished.connect(lambda ok: QTimer.singleShot(2500, capture) if ok else
                                  finish({"ok": False, "error": "The publication page did not load. Try Sign in / reconnect."}))
    QTimer.singleShot(600000 if login else 55000, lambda: finish({"ok": False, "error": "The publication browser timed out."}))
    page.load(QUrl(address))
    app.exec()
    # Destroy the profile before writing success so session cookies are flushed.
    import shiboken6
    shiboken6.delete(page)
    shiboken6.delete(profile)
    private_write(request_path.with_suffix(".result.json"), json.dumps(output))
    return 0
