"""Shared pytest cleanup for Qt widgets left alive by GUI tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def destroy_qt_widgets_before_shutdown():
    """Delete remaining top-level widgets while the Qt event loop is alive."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield

    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:  # Qt is optional for core-only environments.
        return

    app = QApplication.instance()
    if app is None:
        return
    for widget in list(app.topLevelWidgets()):
        try:
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            # The test or its parent already destroyed this wrapper.
            continue
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
