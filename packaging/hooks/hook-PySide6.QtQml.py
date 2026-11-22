"""Keep QML's binding dependencies, without the unrelated QML plugin catalogue.

Quib27 uses Qt Widgets and WebEngine to display local HTML. It has no QML views.
The WebEngine bindings still need the QML/Quick libraries and Python wrappers.
"""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
