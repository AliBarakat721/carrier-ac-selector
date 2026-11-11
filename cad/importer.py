"""Unified CAD input loader for DXF and DWG drawings.

DXF files are parsed directly by ezdxf. DWG files are converted to a temporary
DXF through the user's installed ODA File Converter, then sent through the same
DXF reader. The original drawing is never modified.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ezdxf.addons import odafc

from .dxf_reader import CadDocument, read_dxf


class DwgImportError(RuntimeError):
    """Raised when a DWG file cannot be converted into a readable DXF."""


def _configure_converter() -> None:
    """Allow an explicit ODA executable path without changing global config files."""
    executable = os.environ.get("ODA_FILE_CONVERTER", "").strip()
    if not executable:
        return
    if os.name == "nt":
        odafc.win_exec_path = executable
    else:
        odafc.unix_exec_path = executable


def read_cad_file(path: str | Path) -> CadDocument:
    """Load a DXF directly or convert a DWG temporarily before reading it."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"CAD file not found: {source}")

    suffix = source.suffix.lower()
    if suffix == ".dxf":
        return read_dxf(source)
    if suffix != ".dwg":
        raise ValueError(
            f"Unsupported CAD format: {source.suffix or '(no extension)'}. "
            "Choose a DWG or DXF file."
        )

    _configure_converter()
    try:
        if not odafc.is_installed():
            raise DwgImportError(
                "DWG import requires ODA File Converter. Install it from "
                "https://www.opendesign.com/guestfiles/oda_file_converter "
                "and restart Carrier AC Selector. On Linux, install xvfb if "
                "the converter cannot start without a display. You can also "
                "set ODA_FILE_CONVERTER to the converter executable path."
            )

        with tempfile.TemporaryDirectory(prefix="carrier_ac_dwg_") as temp_dir:
            converted = Path(temp_dir) / f"{source.stem}.dxf"
            odafc.convert(
                str(source),
                str(converted),
                version="R2018",
                audit=False,
                replace=False,
            )
            if not converted.is_file() or converted.stat().st_size == 0:
                raise DwgImportError(
                    "ODA File Converter finished without producing a valid DXF. "
                    "Check that the DWG is readable and supported by your "
                    "installed ODA File Converter version."
                )

            document = read_dxf(converted)
            # Preserve the original source path in the in-memory model.
            document.path = source
            return document
    except DwgImportError:
        raise
    except Exception as exc:
        raise DwgImportError(
            f"Could not convert '{source.name}' to DXF. Verify that ODA File "
            f"Converter is installed and that the drawing is valid. Details: {exc}"
        ) from exc
