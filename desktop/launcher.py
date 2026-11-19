from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import time
import urllib.request


def default_root():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Daily Edition"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Daily Edition"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "daily-edition"


def existing_runtime(root):
    runtime = root / "runtime.json"
    try:
        info = json.loads(runtime.read_text())
        origin = f"http://127.0.0.1:{int(info['port'])}"
        request = urllib.request.Request(origin + "/api/bootstrap", headers={"Cookie": "edition_session=" + info["token"]})
        with urllib.request.urlopen(request, timeout=2) as response:
            if response.status == 200:
                return origin + "/?session=" + info["token"]
    except Exception:
        return None


def native_window(url, service, background=False, screenshot=None):
    # This shell intentionally uses the same local UI as the testable browser view.
    from PySide6.QtCore import QTimer, QUrl, Qt
    from PySide6.QtGui import QAction, QDesktopServices, QIcon
    from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon, QFileDialog, QMessageBox
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Daily Edition")
    app.setDesktopFileName("daily-edition")
    app.setOrganizationName("Daily Edition")
    icon_path = Path(__file__).resolve().parent / "ui/icon.svg"
    app.setWindowIcon(QIcon(str(icon_path)))
    tray_available = QSystemTrayIcon.isSystemTrayAvailable()

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, target, kind, main_frame):
            if target.scheme() in ("http", "https") and target.host() != "127.0.0.1":
                QDesktopServices.openUrl(target)
                return False
            return target.scheme() in ("http", "https", "about", "data")

    class Window(QMainWindow):
        quitting = False

        def closeEvent(self, event):
            active = service and service.store.settings()["schedule_enabled"]
            active = active or bool(service and service.store.query("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1", one=True))
            if tray_available and active and not self.quitting:
                self.hide()
                event.ignore()
                tray.showMessage("Daily Edition", "Your reading schedule is still running. Open the tray menu to quit.")
            else:
                event.accept()
                app.quit()

    window = Window()
    window.setWindowTitle("Daily Edition")
    window.resize(1360, 900)
    window.setMinimumSize(900, 640)
    profile = QWebEngineProfile(window)  # Off-the-record profile; library lives in our database.
    page = Page(profile, window)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
    page.newWindowRequested.connect(lambda request: QDesktopServices.openUrl(request.requestedUrl()) if request.requestedUrl().scheme() in ("http", "https") else None)
    view = QWebEngineView(window)
    view.setPage(page)
    window.setCentralWidget(view)

    def download(request):
        suffix = ".epub" if request.suggestedFileName().endswith(".epub") else ".html"
        file, _ = QFileDialog.getSaveFileName(window, "Save your edition", str(Path.home() / "Downloads" / request.suggestedFileName()), "EPUB book (*.epub)" if suffix == ".epub" else "Web page (*.html)")
        if file:
            request.setDownloadDirectory(str(Path(file).parent))
            request.setDownloadFileName(Path(file).name)
            request.accept()
        else:
            request.cancel()
    profile.downloadRequested.connect(download)

    tray = QSystemTrayIcon(QIcon(str(icon_path)), window)
    tray.setToolTip("Daily Edition · your personal reading")
    menu = QMenu()
    show = QAction("Open Daily Edition", menu)
    show.triggered.connect(lambda: (window.showNormal(), window.raise_(), window.activateWindow()))
    menu.addAction(show)
    menu.addSeparator()
    quit_action = QAction("Quit Daily Edition", menu)
    def quit_app():
        if service and service.store.query("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1", one=True):
            QMessageBox.information(window, "A task is running", "Please let the current task finish before quitting.")
            window.showNormal()
            return
        window.quitting = True
        tray.hide()
        app.quit()
    quit_action.triggered.connect(quit_app)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: show.trigger() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    if tray_available:
        tray.show()
    if not background or not tray_available:
        window.show()
    view.load(QUrl(url))
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(500)
    signal.signal(signal.SIGINT, lambda *_: quit_app())
    signal.signal(signal.SIGTERM, lambda *_: quit_app())
    if screenshot:
        def capture(ok):
            if ok:
                QTimer.singleShot(2500, lambda: (window.grab().save(str(screenshot)), app.quit()))
        view.loadFinished.connect(capture)
    result = app.exec()
    # Delete pages before their off-the-record profile, including screenshot runs.
    import shiboken6
    shiboken6.delete(page)
    shiboken6.delete(profile)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=default_root())
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--headless", action="store_true", help="run the local reading service without a window")
    parser.add_argument("--background", action="store_true", help="start in the system tray")
    parser.add_argument("--no-keyring", action="store_true", help="use private local credential files")
    parser.add_argument("--no-scheduler", action="store_true", help="disable scheduled tasks for this session")
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.data_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    lock = (root / "instance.lock").open("a+")
    service = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        url = existing_runtime(root)
        if not url:
            print("Daily Edition is starting. Please open it again in a moment.", file=sys.stderr)
            return 1
        if args.headless or args.background:
            return 0
    else:
        from .server import Application
        service = Application(root, port=args.port, use_keyring=not args.no_keyring, scheduler=not args.no_scheduler)
        url = service.url
    try:
        if args.headless:
            print("Daily Edition is running locally. Open the desktop app to read or change settings.", flush=True)
            stopped = False
            def stop(*_):
                nonlocal stopped
                stopped = True
            signal.signal(signal.SIGINT, stop)
            signal.signal(signal.SIGTERM, stop)
            while not stopped:
                time.sleep(.25)
            return 0
        return native_window(url, service, args.background, args.screenshot)
    finally:
        if service:
            service.close()
        lock.close()
