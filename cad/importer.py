"""Unified CAD input loader for DXF and DWG drawings.

DXF files are parsed directly by ezdxf. DWG files are converted to a temporary
DXF through the user's installed ODA File Converter, then sent through the same
DXF reader. The original drawing is never modified.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
import ezdxf
from ezdxf.addons import odafc

from .dxf_reader import CadDocument, read_dxf


class DwgImportError(RuntimeError):
    """Raised when a DWG file cannot be converted into a readable DXF."""


def _configure_converter() -> str | None:
    """Configure ezdxf to use an explicit ODA executable path for this process."""
    executable = os.environ.get("ODA_FILE_CONVERTER", "").strip()
    if not executable:
        return None
    executable = str(Path(executable).expanduser().resolve())
    if os.name == "nt":
        ezdxf.options.set("odafc-addon", "win_exec_path", executable)
    else:
        ezdxf.options.set("odafc-addon", "unix_exec_path", executable)
    return executable


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

    configured_converter = _configure_converter()
    try:
        if configured_converter:
            converter_path = Path(configured_converter)
            if not converter_path.is_file():
                raise DwgImportError(
                    f"ODA_FILE_CONVERTER points to a file that does not exist: "
                    f"{configured_converter}"
                )
            if os.name != "nt" and not os.access(converter_path, os.X_OK):
                raise DwgImportError(
                    f"ODA_FILE_CONVERTER is not executable: {configured_converter}. "
                    f"Run chmod +x on the AppImage and try again."
                )
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
