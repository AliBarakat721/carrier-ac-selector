"""Secure GitHub Releases updater for Carrier AC Selector (Windows)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from version import APP_VERSION

OWNER = "AliBarakat721"
REPOSITORY = "carrier-ac-selector"
API_URL = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/releases/latest"
WINDOWS_ASSET = "CarrierACSelector-Windows.zip"
CHECKSUM_ASSET = "SHA256SUMS.txt"
USER_AGENT = "Carrier-AC-Selector-Updater"
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
MAX_MEMBER_BYTES = 150 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250

ALLOWED_ROOT_FILES = {
    "main.py", "core.py", "ocr_engine.py", "electrical_engine.py",
    "roi_tab.py", "room_review_dialog.py", "updater.py", "version.py",
    "apply_update.ps1",
    "Start_Carrier.bat", "setup.ps1", "run.ps1", "test.ps1",
    "requirements.txt", "requirements-dev.txt", "README.md",
}
ALLOWED_DATA_FILES = {"Carrier_AC_Database.xlsx", "Carrier_AC_Database.csv"}
ALLOWED_CAD_FILES = {
    "__init__.py", "block_manager.py", "dxf_exporter.py", "dxf_reader.py",
    "importer.py", "layer_analyzer.py", "layer_manager.py", "room_matching.py",
    "room_review.py", "schedule_builder.py", "space_detector.py",
    "tag_config.py", "workflow.py",
}
# Phase 1 architecture packages. Each is allow-listed file-by-file (like cad/),
# because ``core.py`` imports them: an update missing any of them would crash.
ALLOWED_PACKAGE_FILES = {
    "cad": ALLOWED_CAD_FILES,
    "engineering": {"__init__.py", "units.py"},
    "equipment": {"__init__.py", "best_pick.py", "catalog_reader.py",
                  "indoor_selector.py"},
    "hap": {"__init__.py", "parser.py"},
    "reports": {"__init__.py", "electrical_report.py", "excel_export.py",
                "export_frames.py", "file_io.py"},
    "ui": {
        "__init__.py", "cad_workspace.py", "layout.py", "selection_workspace.py",
    },
}
SEMVER_PATTERN = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")


class UpdateError(RuntimeError):
    """Raised when an update cannot be checked, verified, or started."""


def _parse_version(value: str) -> tuple[int, int, int]:
    value = str(value).strip()
    if value.startswith("v"):
        value = value[1:]
    match = SEMVER_PATTERN.fullmatch(value)
    if not match:
        raise UpdateError(f"Unsupported version format: {value!r}")
    return tuple(int(part) for part in match.groups())


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    return _parse_version(candidate) > _parse_version(current)


def _request(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                "No published GitHub Release was found yet. Please try again later."
            ) from exc
        raise UpdateError(f"GitHub returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Could not connect to GitHub: {exc}") from exc


def _validate_asset_url(url: str) -> None:
    if not isinstance(url, str) or not url:
        raise UpdateError("The release contains an invalid download URL.")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "github.com", "api.github.com"
    }:
        raise UpdateError("The release contains an unexpected download URL.")


def check_for_updates() -> dict:
    """Return metadata for the latest stable release."""
    try:
        release = json.loads(_request(API_URL).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned invalid release metadata.") from exc

    if not isinstance(release, dict):
        raise UpdateError("GitHub returned an invalid release response.")
    if release.get("draft") or release.get("prerelease"):
        raise UpdateError("The latest release is not a stable published release.")

    tag = str(release.get("tag_name", "")).strip()
    if not tag.startswith("v"):
        raise UpdateError("The release tag must use the vMAJOR.MINOR.PATCH format.")
    newer = is_newer_version(tag, APP_VERSION)

    raw_assets = release.get("assets", [])
    if not isinstance(raw_assets, list):
        raise UpdateError("GitHub returned an invalid release asset list.")
    assets = {
        item.get("name"): item
        for item in raw_assets
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    asset = assets.get(WINDOWS_ASSET)
    checksum = assets.get(CHECKSUM_ASSET)
    if newer and (not asset or not checksum):
        raise UpdateError(
            "A newer release exists, but its Windows update package or checksum is missing."
        )

    asset_url = asset.get("browser_download_url") if asset else None
    checksum_url = checksum.get("browser_download_url") if checksum else None
    if newer:
        _validate_asset_url(asset_url or "")
        _validate_asset_url(checksum_url or "")

    return {
        "current_version": APP_VERSION,
        "latest_version": tag[1:],
        "tag_name": tag,
        "update_available": newer,
        "release_notes": release.get("body") or "No release notes were provided.",
        "html_url": release.get("html_url", ""),
        "asset_url": asset_url,
        "checksum_url": checksum_url,
    }


def _download(url: str, destination: Path, max_bytes: int = MAX_ARCHIVE_BYTES) -> None:
    _validate_asset_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
    )
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise UpdateError("The update download exceeds the allowed size.")
            total = 0
            with temporary.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise UpdateError("The update download exceeds the allowed size.")
                    output.write(chunk)
        temporary.replace(destination)
    except UpdateError:
        temporary.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"Update download failed: {exc}") from exc


def _expected_checksum(checksum_path: Path) -> str:
    try:
        lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise UpdateError("Could not read the release checksum file.") from exc
    for line in lines:
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == WINDOWS_ASSET:
            expected = parts[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", expected):
                return expected
    raise UpdateError("The release checksum file has no valid SHA-256 entry for the package.")


def _verify_sha256(archive_path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    try:
        with archive_path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise UpdateError(f"Could not read the downloaded update package: {exc}") from exc
    if digest.hexdigest() != expected.lower():
        raise UpdateError("The downloaded update failed SHA-256 verification.")


def _validate_archive(archive_path: Path) -> None:
    """Reject unsafe, unexpected, duplicate, corrupt, or oversized ZIP members."""
    seen: set[str] = set()
    total_size = 0
    required = {"main.py", "core.py", "requirements.txt", "updater.py",
                "room_review_dialog.py", "version.py", "apply_update.ps1"}
    for package, names in ALLOWED_PACKAGE_FILES.items():
        required.update(f"{package}/{name}" for name in names)
    found: set[str] = set()

    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members:
                raise UpdateError("The downloaded update package is empty.")

            for member in members:
                raw_name = member.filename
                if not raw_name or "\x00" in raw_name or "\\" in raw_name:
                    raise UpdateError("The update package contains an invalid file path.")
                if raw_name.startswith("/") or re.match(r"^[A-Za-z]:", raw_name):
                    raise UpdateError("The update package contains an absolute file path.")

                is_dir = member.is_dir()
                clean_name = raw_name[:-1] if is_dir else raw_name
                raw_parts = clean_name.split("/")
                path = PurePosixPath(clean_name)
                parts = path.parts
                if not parts or any(part in {"", ".", ".."} for part in raw_parts):
                    raise UpdateError("The update package contains an unsafe file path.")
                if any(":" in part or part.endswith((" ", ".")) for part in parts):
                    raise UpdateError("The update package contains an invalid Windows path.")
                if any(re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p)
                       for p in parts):
                    raise UpdateError("The update package contains a reserved Windows filename.")

                key = "/".join(parts).casefold()
                if key in seen:
                    raise UpdateError("The update package contains duplicate file paths.")
                seen.add(key)

                mode = member.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if file_type == stat.S_IFLNK or (
                    file_type and file_type not in {stat.S_IFREG, stat.S_IFDIR}
                ):
                    raise UpdateError("The update package contains a link or special file.")
                if member.flag_bits & 0x1:
                    raise UpdateError("Encrypted update package entries are not supported.")

                if is_dir:
                    if parts == ("data",):
                        continue
                    if (
                        parts[0] in ALLOWED_PACKAGE_FILES
                        and all(part.casefold() != "__pycache__" for part in parts)
                    ):
                        continue
                    else:
                        raise UpdateError(f"Unexpected directory in update package: {raw_name}")
                if len(parts) == 1 and parts[0] in ALLOWED_ROOT_FILES:
                    found.add(parts[0])
                elif len(parts) == 2 and parts[0] == "data" and parts[1] in ALLOWED_DATA_FILES:
                    pass
                elif (len(parts) == 2 and parts[0] in ALLOWED_PACKAGE_FILES
                      and parts[1] in ALLOWED_PACKAGE_FILES[parts[0]]):
                    found.add("/".join(parts))
                elif (
                    len(parts) > 2
                    and parts[0] in ALLOWED_PACKAGE_FILES
                    and parts[-1].casefold().endswith(".py")
                    and all(part.casefold() != "__pycache__" for part in parts)
                ):
                    pass
                else:
                    raise UpdateError(f"Unexpected file in update package: {raw_name}")

                if member.file_size > MAX_MEMBER_BYTES:
                    raise UpdateError("An update package file exceeds the allowed size.")
                total_size += member.file_size
                if total_size > MAX_ARCHIVE_BYTES:
                    raise UpdateError("The expanded update package exceeds the allowed size.")
                if member.compress_size == 0 and member.file_size > 0:
                    raise UpdateError("The update package has an invalid compression ratio.")
                if member.compress_size > 0 and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                    raise UpdateError("The update package has an unsafe compression ratio.")

            missing = required - found
            if missing:
                raise UpdateError(
                    "The update package is missing required files: " + ", ".join(sorted(missing))
                )
            bad_member = archive.testzip()
            if bad_member:
                raise UpdateError(f"The update package contains a corrupt file: {bad_member}")
    except UpdateError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise UpdateError("The downloaded update is not a valid ZIP archive or could not be read.") from exc


def download_and_launch_update(release: dict, app_dir: str, wait_pid: int) -> None:
    """Download, verify, and launch the detached Windows update helper."""
    if os.name != "nt":
        raise UpdateError("In-app installation is currently supported on Windows only.")
    if not release.get("asset_url") or not release.get("checksum_url"):
        raise UpdateError("The release does not contain the required update assets.")

    temp_dir = Path(tempfile.mkdtemp(prefix="carrier-ac-update-"))
    archive_path = temp_dir / WINDOWS_ASSET
    checksum_path = temp_dir / CHECKSUM_ASSET
    try:
        _download(release["asset_url"], archive_path)
        _download(release["checksum_url"], checksum_path, max_bytes=64 * 1024)
        expected = _expected_checksum(checksum_path)
        _verify_sha256(archive_path, expected)
        _validate_archive(archive_path)

        helper = Path(app_dir) / "apply_update.ps1"
        if not helper.is_file():
            raise UpdateError(
                "The update helper is missing. Reinstall the current application first."
            )

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(helper),
                "-Archive", str(archive_path),
                "-TargetDir", str(Path(app_dir).resolve()),
                "-WaitPid", str(int(wait_pid)),
                "-Restart",
            ],
            cwd=app_dir,
            creationflags=creation_flags,
            close_fds=True,
        )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
