"""Shared pytest configuration.

Qt teardown
-----------
Several GUI tests build a ``CadWorkspace`` (or another top-level widget) without
a parent and only call ``close()``. ``close()`` hides a widget but does not
destroy it, so Qt keeps owning the widget and all of its children while Python
still holds the wrapper.

At interpreter shutdown Python then garbage-collects those wrappers, and Qt
reports::

    QObject: shared QObject was deleted directly. The program is malformed and may crash.

followed by heap corruption (``malloc_consolidate(): unaligned fastbin chunk
detected``). The whole pytest process aborts with SIGABRT (exit code 134) even
though every test passed, which turns the CI run red for no visible reason.

The session-scoped finalizer below destroys the remaining top-level widgets
while the Qt event loop is still alive, which removes the double-free.
"""

from __future__ import annotations

import pytest


def _qt_application():
    """Return the active QApplication, or None when Qt is unavailable."""

    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # pragma: no cover - Qt is optional for the pure layers
        return None
    return QApplication.instance()


@pytest.fixture(scope="session", autouse=True)
def _destroy_qt_widgets_at_session_end():
    """Delete leftover top-level widgets before the interpreter shuts down."""

    yield

    app = _qt_application()
    if app is None:
        return
    for widget in list(app.topLevelWidgets()):
        try:
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            # Already destroyed by its owner; nothing left to clean up.
            continue
    app.processEvents()
