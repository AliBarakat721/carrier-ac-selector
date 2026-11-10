import hashlib
import json
import re
import stat
import zipfile

import pytest

import updater
from updater import APP_VERSION, UpdateError, is_newer_version


REQUIRED_FILES = (
    "main.py",
    "core.py",
    "requirements.txt",
    "updater.py",
    "version.py",
    "apply_update.ps1",
)


def _write_valid_archive(path, extra_files=None):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in REQUIRED_FILES:
            archive.writestr(name, "# test package\n")
        for name, payload in (extra_files or {}).items():
            archive.writestr(name, payload)


def test_application_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION)


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("2.0.1", "2.0.0", True),
        ("2.1.0", "2.0.9", True),
        ("3.0.0", "2.99.99", True),
        ("2.0.0", "2.0.0", False),
        ("1.99.99", "2.0.0", False),
        ("v2.0.1", "2.0.0", True),
        ("2.0.0", "2.0.0", False),
    ],
)
def test_version_comparison(candidate, current, expected):
    assert is_newer_version(candidate, current) is expected


@pytest.mark.parametrize(
    "value", ["latest", "2.0", "v2", "2.0.0-beta", "02.0.0", "2.00.0", "2.0.00"]
)
def test_rejects_unsupported_version_format(value):
    with pytest.raises(UpdateError):
        is_newer_version(value, "2.0.0")


def test_check_for_updates_parses_release_and_assets(monkeypatch):
    release = {
        "tag_name": "v2.0.2",
        "body": "Bug fixes",
        "html_url": "https://github.com/AliBarakat721/carrier-ac-selector/releases/tag/v2.0.1",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": updater.WINDOWS_ASSET,
            "browser_download_url": "https://github.com/AliBarakat721/carrier-ac-selector/releases/download/v2.0.2/update.zip",
            },
            {
                "name": updater.CHECKSUM_ASSET,
            "browser_download_url": "https://github.com/AliBarakat721/carrier-ac-selector/releases/download/v2.0.2/SHA256SUMS.txt",
            },
        ],
    }
    monkeypatch.setattr(updater, "_request", lambda _url: json.dumps(release).encode())
    result = updater.check_for_updates()
    assert result["update_available"] is True
    assert result["latest_version"] == "2.0.2"
    assert result["asset_url"].endswith("update.zip")
    assert result["checksum_url"].endswith("SHA256SUMS.txt")


def test_new_release_without_required_assets_is_rejected(monkeypatch):
    release = {"tag_name": "v2.0.2", "assets": []}
    monkeypatch.setattr(updater, "_request", lambda _url: json.dumps(release).encode())
    with pytest.raises(UpdateError, match="package or checksum"):
        updater.check_for_updates()


def test_release_with_untrusted_asset_url_is_rejected(monkeypatch):
    release = {
        "tag_name": "v2.0.2",
        "assets": [
            {"name": updater.WINDOWS_ASSET, "browser_download_url": "https://evil.example/update.zip"},
            {"name": updater.CHECKSUM_ASSET, "browser_download_url": "https://github.com/checksums"},
        ],
    }
    monkeypatch.setattr(updater, "_request", lambda _url: json.dumps(release).encode())
    with pytest.raises(UpdateError, match="unexpected download URL"):
        updater.check_for_updates()


def test_safe_archive_is_accepted(tmp_path):
    archive = tmp_path / "safe.zip"
    _write_valid_archive(
        archive, {"data/Carrier_AC_Database.xlsx": b"user database placeholder"}
    )
    updater._validate_archive(archive)


@pytest.mark.parametrize(
    "member", ["../escape.txt", "/absolute.txt", "C:/escape.txt", "nested/too/deep.py", "main.py:stream"]
)
def test_unsafe_archive_paths_are_rejected(tmp_path, member):
    archive = tmp_path / "unsafe.zip"
    _write_valid_archive(archive, {member: "bad"})
    with pytest.raises(UpdateError):
        updater._validate_archive(archive)


def test_unexpected_file_type_is_rejected(tmp_path):
    archive = tmp_path / "unexpected.zip"
    _write_valid_archive(archive, {"malware.exe": "bad"})
    with pytest.raises(UpdateError, match="Unexpected file"):
        updater._validate_archive(archive)


def test_unexpected_directory_is_rejected(tmp_path):
    archive = tmp_path / "unexpected-directory.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in REQUIRED_FILES:
            zf.writestr(name, "content")
        zf.writestr("payload/", "")
    with pytest.raises(UpdateError, match="Unexpected directory"):
        updater._validate_archive(archive)


def test_missing_required_file_is_rejected(tmp_path):
    archive = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("main.py", "print('ok')")
    with pytest.raises(UpdateError, match="missing required files"):
        updater._validate_archive(archive)


def test_duplicate_case_insensitive_paths_are_rejected(tmp_path):
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in REQUIRED_FILES:
            zf.writestr(name, "content")
        zf.writestr("MAIN.py", "different content")
    with pytest.raises(UpdateError, match="duplicate file paths"):
        updater._validate_archive(archive)


def test_symlink_member_is_rejected(tmp_path):
    archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("main.py")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "C:/outside.txt")
    with pytest.raises(UpdateError, match="link or special file"):
        updater._validate_archive(archive)


def test_invalid_zip_is_rejected(tmp_path):
    archive = tmp_path / "invalid.zip"
    archive.write_bytes(b"not a zip archive")
    with pytest.raises(UpdateError, match="not a valid ZIP"):
        updater._validate_archive(archive)


def test_sha256_verification_accepts_matching_checksum(tmp_path):
    archive = tmp_path / updater.WINDOWS_ASSET
    archive.write_bytes(b"trusted archive bytes")
    checksum = tmp_path / updater.CHECKSUM_ASSET
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(
        f"{digest}  {updater.WINDOWS_ASSET}\n", encoding="utf-8"
    )
    updater._verify_sha256(archive, updater._expected_checksum(checksum))


def test_sha256_verification_rejects_mismatch(tmp_path):
    archive = tmp_path / updater.WINDOWS_ASSET
    archive.write_bytes(b"downloaded archive bytes")
    checksum = tmp_path / updater.CHECKSUM_ASSET
    checksum.write_text(
        "0" * 64 + f"  {updater.WINDOWS_ASSET}\n", encoding="utf-8"
    )
    with pytest.raises(UpdateError, match="SHA-256 verification"):
        updater._verify_sha256(archive, updater._expected_checksum(checksum))


def test_checksum_parser_rejects_malformed_digest(tmp_path):
    checksum = tmp_path / updater.CHECKSUM_ASSET
    checksum.write_text("not-a-hash  CarrierACSelector-Windows.zip\n", encoding="utf-8")
    with pytest.raises(UpdateError, match="no valid SHA-256"):
        updater._expected_checksum(checksum)


def test_download_rejects_untrusted_url(tmp_path):
    with pytest.raises(UpdateError, match="unexpected download URL"):
        updater._download("https://evil.example/package.zip", tmp_path / "package.zip")


def test_download_removes_partial_file_after_interruption(tmp_path, monkeypatch):
    class BrokenResponse:
        headers = {"Content-Length": "10"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            raise OSError("connection interrupted")

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *_a, **_k: BrokenResponse())
    destination = tmp_path / "package.zip"
    with pytest.raises(UpdateError, match="download failed"):
        updater._download(
            "https://github.com/AliBarakat721/carrier-ac-selector/releases/download/v2.0.1/package.zip",
            destination,
        )
    assert not destination.exists()
    assert not destination.with_suffix(".zip.part").exists()


def test_download_rejects_oversized_content_length(tmp_path, monkeypatch):
    class LargeResponse:
        headers = {"Content-Length": str(updater.MAX_ARCHIVE_BYTES + 1)}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *_a, **_k: LargeResponse())
    destination = tmp_path / "package.zip"
    with pytest.raises(UpdateError, match="exceeds the allowed size"):
        updater._download(
            "https://github.com/AliBarakat721/carrier-ac-selector/releases/download/v2.0.1/package.zip",
            destination,
        )
    assert not destination.exists()
