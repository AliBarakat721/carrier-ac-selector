"""Export file helpers: atomic writes, extension handling, default names.

``atomic_export`` / ``replace_export_extension`` / ``export_error_message``
are moved verbatim from ``main.py`` (their ``_``-prefixed names remain
importable from ``main``). No Qt imports.
"""
from __future__ import annotations

import errno
import os
import re
import tempfile
from contextlib import contextmanager


@contextmanager
def atomic_export(path: str):
    """Write an export beside the final path and replace it only on success."""
    final_path = os.path.abspath(path)
    directory = os.path.dirname(final_path) or "."
    suffix = os.path.splitext(final_path)[1] or ".tmp"
    fd, temporary_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(final_path)}.",
        suffix=suffix,
        dir=directory,
    )
    os.close(fd)
    try:
        yield temporary_path
        os.replace(temporary_path, final_path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def replace_export_extension(path: str, required_ext: str) -> str:
    """Replace a textual extension while preserving numeric dotted names."""
    base, suffix = os.path.splitext(path)
    if suffix and suffix[1:].isalpha():
        path = base
    return f"{path}.{required_ext.lstrip('.')}"


def export_error_message(exc: Exception) -> str:
    """Explain common Windows file-lock failures in terms users can fix."""
    if (isinstance(exc, PermissionError)
            or getattr(exc, "errno", None) in {errno.EACCES, errno.EPERM, errno.EBUSY}):
        return (
            "The export could not replace the destination file. If it is open in "
            "Excel or Word, close it and try again; also check that you have "
            "permission to write to the selected folder."
        )
    return str(exc)


def normalize_save_path(path, selected_filter):
    """Force the path's extension to match the selected file filter.

    Examples (uploaded G01.png -> source_basename = "G01"):
        "G01"      + Word filter    -> "G01.docx"
        "G01.docs" + Word filter    -> "G01.docx"
        "G01.xlsx" + Word filter    -> "G01.docx"
        "G01"      + CSV filter     -> "G01.csv"
        "G01.xlsx" + Excel filter   -> "G01.xlsx"  (unchanged)
    """
    m = re.search(r'\(\*\.(\w+)\)', selected_filter)
    if not m:
        return path
    required_ext = m.group(1)
    return replace_export_extension(path, required_ext)


def default_export_name(system_name, source_basename, ext="xlsx", suffix=""):
    """Default filename for an export.

    Priority: active HAP system name, then uploaded file base name, then
    ``Carrier_AC_Selection``. Characters illegal on Windows become ``_``.
    """
    raw = (system_name
           or source_basename
           or "Carrier_AC_Selection")
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).strip() or "export"
    return f"{base}{suffix}.{ext}"
