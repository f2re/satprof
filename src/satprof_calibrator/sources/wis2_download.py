from __future__ import annotations

from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
import hashlib
import json

import requests

from .wis2_files import (
    Wis2DownloadResult,
    atomic_write,
    atomic_write_json,
    looks_like_bufr,
    sha256_file,
    utcnow,
)
from .wis2_message import (
    Wis2Error,
    Wis2Notification,
    decode_inline_content,
    notification_filename,
    parse_notification,
    verify_integrity,
)

_DEFAULT_MEDIA_TYPES = {
    "application/bufr",
    "application/x-bufr",
    "application/octet-stream",
}


class Wis2Downloader:
    def __init__(
        self,
        target_dir: str | Path,
        *,
        allowed_media_types: Iterable[str] | None = None,
        timeout_seconds: int = 90,
        max_bytes: int = 128 * 1024 * 1024,
        verify_tls: bool = True,
        require_bufr_magic: bool = True,
        session: requests.Session | None = None,
    ):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.allowed_media_types = {
            str(item).split(";", 1)[0].strip().lower()
            for item in (allowed_media_types or _DEFAULT_MEDIA_TYPES)
        }
        self.timeout_seconds = int(timeout_seconds)
        self.max_bytes = int(max_bytes)
        self.verify_tls = bool(verify_tls)
        self.require_bufr_magic = bool(require_bufr_magic)
        self.session = session or requests.Session()

    def process(self, topic: str, payload: bytes | str | dict) -> Wis2DownloadResult:
        notification = parse_notification(payload)
        target = self.target_dir / notification_filename(notification)
        sidecar = target.with_name(target.name + ".wis2.json")

        if notification.relation == "deletion":
            existed = target.exists() or sidecar.exists()
            target.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
            return Wis2DownloadResult("deleted", target if existed else None, notification, source="notification")
        if not looks_like_bufr(notification, self.allowed_media_types):
            return Wis2DownloadResult("filtered", None, notification, source="notification")

        if notification.relation != "update" and target.exists() and sidecar.exists():
            try:
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                actual_sha256 = sha256_file(target)
                same_resource = metadata.get("data_id") == notification.data_id and metadata.get("sha256") == actual_sha256
                same_integrity = (
                    not notification.integrity_value
                    or (
                        metadata.get("integrity_method") == notification.integrity_method
                        and metadata.get("integrity_value") == notification.integrity_value
                    )
                )
                if same_resource and same_integrity:
                    return Wis2DownloadResult(
                        "skipped", target, notification, target.stat().st_size, actual_sha256, "existing"
                    )
            except (OSError, json.JSONDecodeError):
                pass

        inline = decode_inline_content(notification)
        if inline is not None:
            if len(inline) > self.max_bytes:
                raise Wis2Error(f"Inline-объект превышает лимит {self.max_bytes} байт")
            data, source = inline, "inline"
        else:
            data, source = self._download(notification), "http"
        verify_integrity(data, notification.integrity_method, notification.integrity_value)
        if self.require_bufr_magic and not data.startswith(b"BUFR"):
            raise Wis2Error("Полученный объект не начинается с сигнатуры BUFR")

        digest = hashlib.sha256(data).hexdigest()
        atomic_write(target, data)
        metadata = {
            "schema": "satprof.wis2-download/1",
            "downloaded_at": utcnow(),
            "topic": topic,
            "message_id": notification.message_id,
            "data_id": notification.data_id,
            "relation": notification.relation,
            "href": notification.href,
            "media_type": notification.media_type,
            "published_at": notification.published_at,
            "observed_at": notification.observed_at,
            "bytes": len(data),
            "sha256": digest,
            "integrity_method": notification.integrity_method,
            "integrity_value": notification.integrity_value,
            "source": source,
            "notification": notification.raw,
        }
        atomic_write_json(sidecar, metadata)
        return Wis2DownloadResult("downloaded", target, notification, len(data), digest, source)

    def _download(self, notification: Wis2Notification) -> bytes:
        if not notification.href:
            raise Wis2Error("В сообщении нет URL для загрузки")
        parsed = urlparse(notification.href)
        if parsed.scheme not in {"http", "https"}:
            raise Wis2Error(f"SatProf поддерживает загрузку WIS2 только по HTTP(S), получено: {parsed.scheme}")
        chunks: list[bytes] = []
        size = 0
        with self.session.get(
            notification.href,
            timeout=self.timeout_seconds,
            stream=True,
            verify=self.verify_tls,
            headers={
                "User-Agent": "SatProf-WIS2/0.4",
                "Accept": notification.media_type or "application/bufr, application/octet-stream;q=0.8",
            },
        ) as response:
            response.raise_for_status()
            announced = response.headers.get("Content-Length")
            if announced and int(announced) > self.max_bytes:
                raise Wis2Error(f"Ресурс превышает лимит {self.max_bytes} байт")
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > self.max_bytes:
                    raise Wis2Error(f"Ресурс превышает лимит {self.max_bytes} байт")
                chunks.append(chunk)
        return b"".join(chunks)
