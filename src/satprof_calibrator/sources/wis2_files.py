from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import tempfile

from .wis2_message import Wis2Notification


@dataclass(frozen=True)
class Wis2DownloadResult:
    status: str
    path: Path | None
    notification: Wis2Notification
    bytes_written: int = 0
    sha256: str | None = None
    source: str | None = None


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def looks_like_bufr(notification: Wis2Notification, allowed_media_types: set[str]) -> bool:
    if notification.media_type in allowed_media_types:
        return True
    name = (notification.data_id + " " + (notification.href or "")).lower()
    return any(token in name for token in (".bufr", ".bufr4", "temp_", "_temp"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: dict) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write(path, content)
