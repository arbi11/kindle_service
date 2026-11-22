"""Collect QtGui's normal runtime without its unused virtual keyboard plugin."""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [(source, destination) for source, destination in binaries
            if 'virtualkeyboard' not in Path(source).name.lower()]
