from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse
import base64
import gzip
import hashlib
import hmac
import json
import re

_HASHLIB_NAMES = {
    "sha256": "sha256",
    "sha384": "sha384",
    "sha512": "sha512",
    "sha3-256": "sha3_256",
    "sha3-384": "sha3_384",
    "sha3-512": "sha3_512",
}


class Wis2Error(RuntimeError):
    """Ошибка при разборе или загрузке сообщения WIS 2.0."""


@dataclass(frozen=True)
class Wis2Notification:
    message_id: str
    data_id: str
    relation: str
    href: str | None
    media_type: str | None
    published_at: str | None
    observed_at: str | None
    integrity_method: str | None
    integrity_value: str | None
    content_encoding: str | None
    content_value: str | None
    content_size: int | None
    geometry: dict[str, Any] | None
    raw: dict[str, Any]


def parse_notification(payload: bytes | str | dict[str, Any]) -> Wis2Notification:
    if isinstance(payload, dict):
        obj = payload
    else:
        try:
            text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
            obj = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Wis2Error("Сообщение WIS2 не является корректным UTF-8 JSON") from exc
    if not isinstance(obj, dict) or obj.get("type") != "Feature":
        raise Wis2Error("WIS2 notification должен быть GeoJSON Feature")
    message_id = str(obj.get("id") or "").strip()
    properties = obj.get("properties")
    links = obj.get("links")
    if not message_id or not isinstance(properties, dict) or not isinstance(links, list):
        raise Wis2Error("В сообщении отсутствуют id, properties или links")
    data_id = str(properties.get("data_id") or "").strip()
    if not data_id:
        raise Wis2Error("В сообщении отсутствует properties.data_id")

    selected: dict[str, Any] | None = None
    for relation in ("canonical", "update", "deletion"):
        selected = next(
            (item for item in links if isinstance(item, dict) and item.get("rel") == relation),
            None,
        )
        if selected is not None:
            break
    if selected is None:
        raise Wis2Error("Не найдена ссылка canonical, update или deletion")

    relation = str(selected.get("rel"))
    href = str(selected.get("href") or "").strip() or None
    media_type = str(selected.get("type") or "").split(";", 1)[0].strip().lower() or None
    if relation != "deletion" and href is None and not isinstance(properties.get("content"), dict):
        raise Wis2Error("Для нового ресурса не задана ссылка или inline content")

    integrity = properties.get("integrity") if isinstance(properties.get("integrity"), dict) else {}
    content = properties.get("content") if isinstance(properties.get("content"), dict) else {}
    try:
        content_size = int(content["size"]) if content.get("size") is not None else None
    except (TypeError, ValueError):
        content_size = None

    return Wis2Notification(
        message_id=message_id,
        data_id=data_id,
        relation=relation,
        href=href,
        media_type=media_type,
        published_at=properties.get("pubtime"),
        observed_at=properties.get("datetime") or properties.get("start_datetime"),
        integrity_method=str(integrity.get("method") or "").lower() or None,
        integrity_value=str(integrity.get("value") or "").strip() or None,
        content_encoding=str(content.get("encoding") or "").lower() or None,
        content_value=content.get("value") if isinstance(content.get("value"), str) else None,
        content_size=content_size,
        geometry=obj.get("geometry") if isinstance(obj.get("geometry"), dict) else None,
        raw=obj,
    )


def decode_inline_content(notification: Wis2Notification) -> bytes | None:
    if notification.content_value is None:
        return None
    encoding = notification.content_encoding or "utf-8"
    value = notification.content_value
    try:
        if encoding == "utf-8":
            data = value.encode("utf-8")
        elif encoding == "base64":
            data = base64.b64decode(value, validate=True)
        elif encoding == "gzip":
            data = gzip.decompress(base64.b64decode(value, validate=True))
        else:
            raise Wis2Error(f"Неподдерживаемое inline-кодирование WIS2: {encoding}")
    except (ValueError, OSError) as exc:
        raise Wis2Error(f"Не удалось декодировать inline content ({encoding})") from exc
    if notification.content_size is not None:
        encoded_size = len(value.encode("utf-8"))
        if notification.content_size not in {len(data), encoded_size}:
            raise Wis2Error(
                f"Размер inline content не совпадает: объявлено {notification.content_size}, "
                f"декодировано {len(data)}, закодировано {encoded_size}"
            )
    return data


def verify_integrity(data: bytes, method: str | None, expected_value: str | None) -> None:
    if not method and not expected_value:
        return
    if not method or not expected_value:
        raise Wis2Error("Неполное описание properties.integrity")
    name = _HASHLIB_NAMES.get(method.lower())
    if name is None:
        raise Wis2Error(f"Неподдерживаемый алгоритм контроля целостности: {method}")
    try:
        expected = base64.b64decode(expected_value, validate=True)
    except ValueError as exc:
        raise Wis2Error("Контрольная сумма WIS2 должна быть закодирована base64") from exc
    actual = hashlib.new(name, data).digest()
    if not hmac.compare_digest(actual, expected):
        raise Wis2Error(f"Контрольная сумма {method} не совпадает")


def parse_broker_url(value: str) -> dict[str, Any]:
    parsed = urlparse(value)
    if parsed.scheme not in {"mqtt", "mqtts"} or not parsed.hostname:
        raise Wis2Error("broker должен иметь вид mqtt[s]://user:password@host:port")
    return {
        "host": parsed.hostname,
        "port": parsed.port or (8883 if parsed.scheme == "mqtts" else 1883),
        "username": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else None,
        "tls": parsed.scheme == "mqtts",
    }


def _safe_name(value: str) -> str:
    value = unquote(value).replace("\\", "/").rstrip("/").split("/")[-1]
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value[:180] or "wis2-data.bufr4"


def notification_filename(notification: Wis2Notification) -> str:
    digest = hashlib.sha256(notification.data_id.encode("utf-8")).hexdigest()[:16]
    candidate = _safe_name(notification.data_id)
    if "." not in candidate:
        href_name = _safe_name(urlparse(notification.href or "").path)
        candidate = href_name if "." in href_name else f"{candidate}.bufr4"
    return f"{digest}_{candidate}"
