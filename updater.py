"""GitHub Releases updater for Carrier AC Selector (Windows)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from version import APP_VERSION

OWNER = "AliBarakat721"
REPOSITORY = "carrier-ac-selector"
API_URL = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/releases/latest"
WINDOWS_ASSET = "CarrierACSelector-Windows.zip"
CHECKSUM_ASSET = "SHA256SUMS.txt"
USER_AGENT = "Carrier-AC-Selector-Updater"


class UpdateError(RuntimeError):
    """Raised when an update cannot be checked, verified, or started."""


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


def _version_key(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise UpdateError(f"Unsupported version format: {value!r}")
    return tuple(int(part) for part in match.groups())


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    return _version_key(candidate) > _version_key(current)


def check_for_updates() -> dict:
    """Return latest release metadata and whether it is newer than this install."""
    try:
        release = json.loads(_request(API_URL).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned invalid release metadata.") from exc

    tag = str(release.get("tag_name", "")).strip()
    if not tag:
        raise UpdateError("The latest GitHub Release has no version tag.")
    newer = is_newer_version(tag, APP_VERSION)

    assets = {
        item.get("name"): item
        for item in release.get("assets", [])
        if isinstance(item, dict)
    }
    asset = assets.get(WINDOWS_ASSET)
    checksum = assets.get(CHECKSUM_ASSET)
    if newer and (not asset or not checksum):
        raise UpdateError(
            "A newer release exists, but its Windows update package or checksum is missing."
        )

    return {
        "current_version": APP_VERSION,
        "latest_version": tag.lstrip("v"),
        "tag_name": tag,
        "update_available": newer,
        "release_notes": release.get("body") or "No release notes were provided.",
        "html_url": release.get("html_url", ""),
        "asset_url": asset.get("browser_download_url") if asset else None,
        "checksum_url": checksum.get("browser_download_url") if checksum else None,
    }


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Update download failed: {exc}") from exc


def _validate_archive(archive_path: Path) -> None:
    allowed_suffixes = {".py", ".ps1", ".bat", ".txt", ".md", ".yml", ".xlsx"}
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if not members:
            raise UpdateError("The downloaded update package is empty.")
        for member in members:
            name = member.filename.replace("\\", "/")
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise UpdateError("The update package contains an unsafe file path.")
            if member.is_dir():
                continue
            if path.parts[0] not in {"data"} and (
                len(path.parts) != 1 or path.suffix.lower() not in allowed_suffixes
            ):
                raise UpdateError(f"Unexpected file in update package: {name}")
            if path.parts[0] == "data" and (
                len(path.parts) != 2 or path.suffix.lower() not in {".xlsx", ".csv"}
            ):
                raise UpdateError(f"Unexpected data file in update package: {name}")


def download_and_launch_update(release: dict, app_dir: str, wait_pid: int) -> None:
    """Download, verify, and launch the detached Windows apply-update helper."""
    if os.name != "nt":
        raise UpdateError("In-app installation is currently supported on Windows only.")
    if not release.get("asset_url") or not release.get("checksum_url"):
        raise UpdateError("The release does not contain the required update assets.")

    temp_dir = Path(tempfile.mkdtemp(prefix="carrier-ac-update-"))
    archive_path = temp_dir / WINDOWS_ASSET
    checksum_path = temp_dir / CHECKSUM_ASSET
    _download(release["asset_url"], archive_path)
    _download(release["checksum_url"], checksum_path)

    expected = None
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == WINDOWS_ASSET:
            expected = parts[0].lower()
            break
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise UpdateError("The release checksum file is missing a valid SHA-256 entry.")

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if digest != expected:
        raise UpdateError("The downloaded update failed SHA-256 verification.")
    _validate_archive(archive_path)

    helper = Path(app_dir) / "apply_update.ps1"
    if not helper.is_file():
        raise UpdateError("The update helper is missing. Reinstall the current application first.")

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
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
    except OSError as exc:
        raise UpdateError(f"Could not start the update helper: {exc}") from exc
