"""What the newest release is, proved by its signature rather than by where it was downloaded from.

A release publishes `manifest.json` and `manifest.json.minisig`, signed with the key below, and the
manifest pins the wheel's checksum. Whoever can replace a release asset can replace the manifest
too, so only the signature counts. A signed manifest is replayable, so it is only ever acted on when
its version is strictly newer than the running one — an old signed release can't downgrade anyone.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.core.errors import OpenNetWorthError
from backend.core.pinned_http import https_context
from backend.core.platform_fs import is_current_owner, replace, restrict_to_owner
from backend.core.version import DISTRIBUTION

UPDATE_REPO = "open-net-worth-ai/open-net-worth-releases"
# The same key as UPDATE_PUBLIC_KEY in desktop/src/main/update-key.ts (a test holds them equal).
UPDATE_PUBLIC_KEY = "RWTYU3Ouc5i3Bz3VhkJESnTVaeGFjEzBgdcOtNp0HneVLIPGSWX1BzzJ"
LATEST_DOWNLOAD = f"https://github.com/{UPDATE_REPO}/releases/latest/download"
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_WHEEL_BYTES = 1024 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_TIMEOUT_SECONDS = 60
_KEY_ID_BYTES = 8
_SIGNATURE_BYTES = 64
_RAW_KEY_BYTES = 32
_TRUSTED_PREFIX = "trusted comment: "


class UpgradeError(OpenNetWorthError):
    code = "upgrade_failed"


@dataclass(frozen=True)
class Release:
    version: str
    wheel_name: str
    wheel_sha256: str
    wheel_size: int

    @property
    def wheel_url(self) -> str:
        return (
            f"https://github.com/{UPDATE_REPO}/releases/download/v{self.version}/{self.wheel_name}"
        )


def _decode(text: str, length: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except ValueError as exc:
        raise UpgradeError(f"The release {what} is malformed.") from exc
    if len(raw) != length:
        raise UpgradeError(f"The release {what} is malformed.")
    return raw


def verify_minisign(message: bytes, signature_file: str, public_key: str) -> str:
    """Raise unless `signature_file` signs `message` with `public_key`; return its trusted comment."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = _decode(public_key, 2 + _KEY_ID_BYTES + _RAW_KEY_BYTES, "public key")
    lines = signature_file.splitlines()
    if len(lines) < 4 or not lines[2].startswith(_TRUSTED_PREFIX):
        raise UpgradeError("The release signature is malformed.")
    signature = _decode(lines[1], 2 + _KEY_ID_BYTES + _SIGNATURE_BYTES, "signature")
    algorithm = signature[:2]
    if key[:2] != b"Ed" or algorithm not in (b"Ed", b"ED"):
        raise UpgradeError("The release signature uses an unsupported algorithm.")
    if signature[2 : 2 + _KEY_ID_BYTES] != key[2 : 2 + _KEY_ID_BYTES]:
        raise UpgradeError("The release was signed with a key this install doesn't trust.")
    raw_signature = signature[2 + _KEY_ID_BYTES :]
    trusted = lines[2][len(_TRUSTED_PREFIX) :]
    global_signature = _decode(lines[3], _SIGNATURE_BYTES, "trusted-comment signature")
    signed = hashlib.blake2b(message).digest() if algorithm == b"ED" else message
    verifier = Ed25519PublicKey.from_public_bytes(key[2 + _KEY_ID_BYTES :])
    try:
        verifier.verify(raw_signature, signed)
        verifier.verify(global_signature, raw_signature + trusted.encode("utf-8"))
    except InvalidSignature as exc:
        raise UpgradeError("The release signature is not valid.") from exc
    return trusted


def parse_version(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        raise UpgradeError(f"Can't compare the version {version!r}.")
    return tuple(int(part) for part in version.split("."))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def _open(url: str):
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=https_context()))
    request = urllib.request.Request(url, headers={"User-Agent": DISTRIBUTION})
    return opener.open(request, timeout=_TIMEOUT_SECONDS)


def _fetch(url: str) -> bytes:
    try:
        with _open(url) as response:
            body = response.read(_MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        raise UpgradeError(f"Couldn't reach {url}: {exc}") from exc
    if len(body) > _MAX_MANIFEST_BYTES:
        raise UpgradeError(f"{url} is larger than a release manifest can be.")
    return body


def _release_from_manifest(manifest: object) -> Release:
    try:
        version = manifest["version"]  # type: ignore[index]
        wheel = manifest["python"]["wheel"]  # type: ignore[index]
        name, digest, size = wheel["name"], wheel["sha256"], wheel["size"]
    except (KeyError, TypeError) as exc:
        raise UpgradeError("The release manifest has no wheel.") from exc
    parse_version(str(version))
    wheel_prefix = f"{DISTRIBUTION.replace('-', '_')}-{version}-"
    if not (isinstance(name, str) and name.startswith(wheel_prefix) and name.endswith(".whl")):
        raise UpgradeError("The release manifest names an unexpected wheel.")
    if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
        raise UpgradeError("The release manifest names an unexpected wheel.")
    if not isinstance(size, int) or not 0 < size <= _MAX_WHEEL_BYTES:
        raise UpgradeError("The release manifest gives an impossible wheel size.")
    return Release(str(version), name, str(digest), size)


def latest_release(
    fetch: Callable[[str], bytes] = _fetch, public_key: str = UPDATE_PUBLIC_KEY
) -> Release:
    """The newest published release, or `UpgradeError` when it can't be fetched or proved."""
    body = fetch(f"{LATEST_DOWNLOAD}/manifest.json")
    signature = fetch(f"{LATEST_DOWNLOAD}/manifest.json.minisig")
    try:
        trusted = verify_minisign(body, signature.decode("utf-8"), public_key)
        manifest = json.loads(body)
    except ValueError as exc:
        raise UpgradeError("The release manifest is malformed.") from exc
    release = _release_from_manifest(manifest)
    if trusted != f"Open Net Worth {release.version}":
        raise UpgradeError("The release signature is for a different version.")
    return release


def _private_directory(directory: Path) -> None:
    """Only this user can swap a verified wheel, even if uv's folder lives somewhere shared."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if not is_current_owner(directory.stat()):
            raise UpgradeError(f"{directory} belongs to another user, so it can't hold an upgrade.")
        restrict_to_owner(directory)
    except OSError as exc:
        raise UpgradeError(f"Can't prepare {directory}: {exc}") from exc


def wheel_matches(path: Path, release: Release) -> bool:
    if not path.is_file() or path.stat().st_size != release.wheel_size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest() == release.wheel_sha256


def download_wheel(release: Release, directory: Path, opener: Callable = _open) -> Path:
    """Download the release's wheel into `directory`; the file there always matches the manifest."""
    _private_directory(directory)
    target = directory / release.wheel_name
    if wheel_matches(target, release):
        return target
    partial = directory / f"{release.wheel_name}.part"
    try:
        with opener(release.wheel_url) as response, partial.open("wb") as file:
            written = 0
            while chunk := response.read(_CHUNK_BYTES):
                written += len(chunk)
                if written > release.wheel_size:
                    raise UpgradeError("The downloaded wheel is larger than the release says.")
                file.write(chunk)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise UpgradeError(f"Couldn't download {release.wheel_name}: {exc}") from exc
    except UpgradeError:
        partial.unlink(missing_ok=True)
        raise
    if not wheel_matches(partial, release):
        partial.unlink(missing_ok=True)
        raise UpgradeError("The downloaded wheel doesn't match the signed release.")
    replace(partial, target)
    return target
