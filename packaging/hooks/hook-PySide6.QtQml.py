"""Keep QML's binding dependencies, without the unrelated QML plugin catalogue.

Quib27 uses Qt Widgets and WebEngine to display local HTML. It has no QML views.
The WebEngine bindings still need the QML/Quick libraries and Python wrappers.
"""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# QML debugging/profiling plugins are development tools, not WebEngine runtime
# dependencies. Their Quick3D profiler otherwise pulls in unused Quick3D code.
binaries = [(source, destination) for source, destination in binaries
            if 'qmltooling' not in Path(source).parts]
