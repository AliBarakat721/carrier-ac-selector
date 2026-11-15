"""Portable, crash-tolerant storage for Carrier AC Selector projects.

A project is a directory containing ``project.json`` and project-owned input,
working, review, and export folders. Original user files are copied, never
modified. Manifest paths are always relative to the project root.
"""
from __future__ import annotations

import json
import filecmp
import hashlib
import math
import os
import re
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

PROJECT_FILENAME = "project.json"
PROJECT_FORMAT_VERSION = 1


class ProjectStoreError(RuntimeError):
    """An invalid or inaccessible Carrier project."""


def _json_safe(value: Any) -> Any:
    """Convert common Python, Qt-adjacent, and pandas values to strict JSON."""
    if type(value).__name__ in {"NAType", "NaTType"}:
        return None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value") and isinstance(value.value, (str, int, float, bool)):
        return _json_safe(value.value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_safe(value.item())
        except (ValueError, TypeError):
            pass
    if hasattr(value, "tolist") and callable(value.tolist):
        return _json_safe(value.tolist())
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    raise TypeError(f"Cannot serialize {type(value).__name__} into a project manifest")


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == destination.resolve():
        return
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ProjectStore:
    """Own all paths and serialized state for one relocatable project."""

    def __init__(self, root: str | Path, manifest: dict[str, Any]):
        self.root = Path(root).expanduser().resolve()
        self.manifest = manifest

    @staticmethod
    def normalize(value: Any) -> Any:
        """Return a JSON-safe representation suitable for equality checks."""
        return _json_safe(value)

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.root.name)

    @classmethod
    def create(cls, root: str | Path, name: str | None = None) -> "ProjectStore":
        project_root = Path(root).expanduser().resolve()
        if project_root.exists() and any(project_root.iterdir()):
            raise ProjectStoreError(f"Project folder is not empty: {project_root}")
        project_root.mkdir(parents=True, exist_ok=True)
        for folder in ("cad", "hap", "review", "exports", "backups"):
            (project_root / folder).mkdir(exist_ok=True)
        manifest = {
            "format": "carrier-ac-selector-project",
            "format_version": PROJECT_FORMAT_VERSION,
            "name": name or project_root.name,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "files": {"cad_original": None, "cad_working": None, "hap_source": None},
            "state": {},
        }
        store = cls(project_root, manifest)
        store.save()
        return store

    @classmethod
    def open(cls, root: str | Path) -> "ProjectStore":
        project_root = Path(root).expanduser().resolve()
        manifest_path = project_root / PROJECT_FILENAME
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as primary_error:
            backup = project_root / f"{PROJECT_FILENAME}.bak"
            try:
                manifest = json.loads(backup.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise ProjectStoreError(
                    f"Cannot read project manifest or its backup in {project_root}: {primary_error}"
                ) from primary_error
        if manifest.get("format") != "carrier-ac-selector-project":
            raise ProjectStoreError("This folder is not a Carrier AC Selector project.")
        version = manifest.get("format_version")
        if version != PROJECT_FORMAT_VERSION:
            raise ProjectStoreError(
                f"Unsupported project format version {version!r}; expected {PROJECT_FORMAT_VERSION}."
            )
        store = cls(project_root, manifest)
        for key, relative in manifest.get("files", {}).items():
            if relative:
                store.resolve(relative, must_exist=True)
        return store

    def resolve(self, relative: str | Path, *, must_exist: bool = False) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ProjectStoreError(f"Project path escapes the project directory: {relative}") from exc
        if must_exist and not candidate.is_file():
            raise ProjectStoreError(f"Project asset is missing: {relative}")
        return candidate

    def add_source(self, kind: str, source: str | Path) -> Path:
        """Copy a source file into the project and return its owned path."""
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise ProjectStoreError(f"Source file does not exist: {source_path}")
        if kind == "cad":
            folder = Path("cad") / "sources"
            key = "cad_original"
        elif kind == "hap":
            folder = Path("hap") / "sources"
            key = "hap_source"
        else:
            raise ValueError("kind must be 'cad' or 'hap'")
        source_stem = re.sub(r"[^\w.-]+", "_", source_path.stem, flags=re.UNICODE).strip("._") or "source"
        digest = _sha256(source_path)[:12]
        relative = folder / f"{source_stem[:80]}-{digest}{source_path.suffix.lower()}"
        destination = self.resolve(relative)
        _copy_atomic(source_path, destination)
        self.manifest.setdefault("files", {})[key] = relative.as_posix()
        return destination

    def set_working_dxf(self, drawing: Any) -> Path:
        """Persist an ezdxf Drawing as the project-owned working DXF."""
        destination = self.root / "cad" / "working.dxf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".working-{os.getpid()}.tmp.dxf"
        try:
            drawing.saveas(str(temporary))
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise ProjectStoreError("CAD conversion produced an empty working DXF.")
            return self.commit_working_dxf(temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def commit_working_dxf(self, source: str | Path) -> Path:
        """Atomically replace the working DXF after retaining a bounded backup."""
        candidate = Path(source).expanduser().resolve()
        if not candidate.is_file() or candidate.suffix.lower() != ".dxf":
            raise ProjectStoreError("Working CAD asset must be an existing DXF file.")
        destination = self.root / "cad" / "working.dxf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if candidate != destination:
            if destination.is_file() and not filecmp.cmp(candidate, destination, shallow=False):
                backup_dir = self.root / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / f"working-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.dxf"
                _copy_atomic(destination, backup)
                old_backups = sorted(backup_dir.glob("working-*.dxf"), key=lambda path: path.stat().st_mtime, reverse=True)
                for stale in old_backups[5:]:
                    stale.unlink(missing_ok=True)
            os.replace(candidate, destination)
        self.manifest.setdefault("files", {})["cad_working"] = "cad/working.dxf"
        return destination

    def record_working_dxf(self, path: str | Path) -> Path:
        """Register a DXF already atomically written by the CAD exporter."""
        candidate = Path(path).expanduser().resolve()
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise ProjectStoreError("Working DXF must be stored inside the project.") from exc
        if relative.suffix.lower() != ".dxf" or not candidate.is_file():
            raise ProjectStoreError("Working CAD asset must be an existing DXF file.")
        self.manifest.setdefault("files", {})["cad_working"] = relative.as_posix()
        return candidate

    def asset(self, key: str) -> Path | None:
        relative = self.manifest.get("files", {}).get(key)
        return self.resolve(relative, must_exist=True) if relative else None

    def update_state(self, state: Mapping[str, Any]) -> None:
        self.manifest["state"] = _json_safe(state)
        self.save()

    def save(self) -> None:
        """Atomically write the manifest, retaining the last valid version."""
        self.root.mkdir(parents=True, exist_ok=True)
        manifest_path = self.root / PROJECT_FILENAME
        if manifest_path.exists():
            backup = self.root / f"{PROJECT_FILENAME}.bak"
            fd, backup_tmp = tempfile.mkstemp(prefix=".project-backup-", dir=self.root)
            os.close(fd)
            try:
                shutil.copy2(manifest_path, backup_tmp)
                os.replace(backup_tmp, backup)
            except Exception:
                Path(backup_tmp).unlink(missing_ok=True)
                raise
        self.manifest["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        fd, temporary = tempfile.mkstemp(prefix=".project-manifest-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, manifest_path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    @property
    def state(self) -> dict[str, Any]:
        return dict(self.manifest.get("state") or {})
