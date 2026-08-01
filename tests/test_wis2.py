from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from satprof_calibrator.sources.wis2 import (
    Wis2Downloader,
    Wis2Error,
    decode_inline_content,
    notification_filename,
    parse_broker_url,
    parse_notification,
    verify_integrity,
)


def notification(data: bytes, *, encoding: str = "base64", method: str = "sha256") -> dict:
    if encoding == "base64":
        value = base64.b64encode(data).decode()
    elif encoding == "gzip":
        value = base64.b64encode(gzip.compress(data)).decode()
    else:
        value = data.decode()
    digest = hashlib.new(method.replace("-", "_"), data).digest()
    return {
        "id": "31e9d66a-cd83-4174-9429-b932f1abcdef",
        "conformsTo": ["http://wis.wmo.int/spec/wnm/1/conf/core"],
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [30.3, 59.9]},
        "properties": {
            "pubtime": "2026-08-01T12:00:00Z",
            "datetime": "2026-08-01T11:30:00Z",
            "data_id": "wis2/de-dwd/data/core/weather/surface-based-observations/temp/TEMP_123.bufr4",
            "integrity": {"method": method, "value": base64.b64encode(digest).decode()},
            "content": {"encoding": encoding, "value": value, "size": len(data)},
        },
        "links": [{"href": "https://cache.example/TEMP_123.bufr4", "rel": "canonical", "type": "application/bufr"}],
    }


def test_parse_and_inline_base64():
    raw = b"BUFR-test-payload"
    parsed = parse_notification(notification(raw))
    assert parsed.data_id.endswith("TEMP_123.bufr4")
    assert parsed.relation == "canonical"
    assert decode_inline_content(parsed) == raw
    verify_integrity(raw, parsed.integrity_method, parsed.integrity_value)


def test_inline_gzip():
    raw = b"BUFR" * 100
    assert decode_inline_content(parse_notification(notification(raw, encoding="gzip"))) == raw


def test_integrity_failure():
    with pytest.raises(Wis2Error, match="не совпадает"):
        verify_integrity(b"wrong", "sha256", base64.b64encode(hashlib.sha256(b"right").digest()).decode())


def test_downloader_atomic_and_deduplicated(tmp_path: Path):
    raw = b"BUFR\x00\x01"
    message = notification(raw)
    downloader = Wis2Downloader(tmp_path)
    first = downloader.process("cache/a/wis2/de-dwd/data/core/weather/surface-based-observations/temp", message)
    second = downloader.process("cache/a/wis2/other/data/core/weather/surface-based-observations/temp", message)
    assert first.status == "downloaded"
    assert first.path and first.path.read_bytes() == raw
    assert second.status == "skipped" and second.path == first.path
    sidecar = json.loads(first.path.with_name(first.path.name + ".wis2.json").read_text())
    assert sidecar["data_id"] == message["properties"]["data_id"]
    assert sidecar["sha256"] == hashlib.sha256(raw).hexdigest()
    assert not list(tmp_path.glob("*.part"))


def test_filter_non_bufr(tmp_path: Path):
    message = notification(b"text")
    message["properties"]["data_id"] = "wis2/test/image.png"
    message["links"][0].update(href="https://cache.example/image.png", type="image/png")
    result = Wis2Downloader(tmp_path).process("topic", message)
    assert result.status == "filtered"
    assert not list(tmp_path.iterdir())


def test_broker_url_and_stable_filename():
    broker = parse_broker_url("mqtts://everyone:secret@broker.example:8883")
    assert broker == {"host": "broker.example", "port": 8883, "username": "everyone", "password": "secret", "tls": True}
    assert notification_filename(parse_notification(notification(b"BUFRx"))).endswith("TEMP_123.bufr4")


def test_reject_invalid_notification():
    with pytest.raises(Wis2Error):
        parse_notification({"type": "Feature", "id": "x", "properties": {}, "links": []})


def test_reject_non_bufr_magic(tmp_path: Path):
    with pytest.raises(Wis2Error, match="сигнатуры BUFR"):
        Wis2Downloader(tmp_path).process("topic", notification(b"not-a-bufr"))


def test_update_replaces_existing_object(tmp_path: Path):
    downloader = Wis2Downloader(tmp_path)
    first = downloader.process("topic", notification(b"BUFR-old"))
    update_message = notification(b"BUFR-new")
    update_message["links"][0]["rel"] = "update"
    update_message["id"] = "update-message-id"
    updated = downloader.process("topic", update_message)
    assert first.path == updated.path
    assert updated.status == "downloaded"
    assert updated.path and updated.path.read_bytes() == b"BUFR-new"


def test_deletion_removes_cached_object(tmp_path: Path):
    downloader = Wis2Downloader(tmp_path)
    message = notification(b"BUFR-delete")
    saved = downloader.process("topic", message)
    deletion = notification(b"BUFR-unused")
    deletion["links"] = [{"href": deletion["links"][0]["href"], "rel": "deletion", "type": "application/bufr"}]
    deletion["properties"].pop("content")
    deletion["properties"].pop("integrity")
    result = downloader.process("topic", deletion)
    assert result.status == "deleted"
    assert saved.path and not saved.path.exists()
    assert not saved.path.with_name(saved.path.name + ".wis2.json").exists()
