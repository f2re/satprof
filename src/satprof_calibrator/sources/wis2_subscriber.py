from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging
import os
import ssl
import threading

from .wis2_download import Wis2Downloader, Wis2DownloadResult
from .wis2_message import Wis2Error, parse_broker_url

LOGGER = logging.getLogger("satprof.wis2")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Wis2Subscriber:
    """Долгоживущая MQTT(S)-подписка на WIS2 Global Broker."""

    def __init__(self, workspace: Any, cfg: dict[str, Any]):
        from ..config import resolve_path

        self.workspace = workspace
        self.cfg = cfg
        self.source_cfg = cfg.get("sources", {}).get("wis2", {})
        self.download_dir = resolve_path(
            cfg,
            self.source_cfg.get("download_dir", "inbox/wis2"),
            workspace_relative=True,
        )
        self.downloader = Wis2Downloader(
            self.download_dir,
            allowed_media_types=self.source_cfg.get("media_types"),
            timeout_seconds=int(self.source_cfg.get("download_timeout_seconds", 90)),
            max_bytes=int(self.source_cfg.get("max_download_bytes", 128 * 1024 * 1024)),
            verify_tls=bool(self.source_cfg.get("verify_tls", True)),
            require_bufr_magic=bool(self.source_cfg.get("require_bufr_magic", True)),
        )
        self.broker = parse_broker_url(os.path.expandvars(str(self.source_cfg.get("broker") or "")))
        username_env = str(self.source_cfg.get("username_env") or "SATPROF_WIS2_USERNAME")
        password_env = str(self.source_cfg.get("password_env") or "SATPROF_WIS2_PASSWORD")
        self.broker["username"] = self.source_cfg.get("username") or os.environ.get(username_env) or self.broker["username"]
        self.broker["password"] = self.source_cfg.get("password") or os.environ.get(password_env) or self.broker["password"]
        self.topics = list(self.source_cfg.get("topics") or [])
        if not self.topics:
            raise Wis2Error("sources.wis2.topics не должен быть пустым")
        node = os.uname().nodename if hasattr(os, "uname") else "host"
        self.client_id = str(self.source_cfg.get("client_id") or f"satprof-{node}-{os.getpid()}")[:120]
        self.qos = int(self.source_cfg.get("qos", 1))
        self.enqueue_sync = bool(self.source_cfg.get("enqueue_sync", True))
        self.stop_event = threading.Event()
        self.stats = {"received": 0, "downloaded": 0, "skipped": 0, "filtered": 0, "deleted": 0, "errors": 0}
        self._client: Any = None

    def _state(self, status: str, **extra: Any) -> None:
        self.workspace.set_state(
            "source.wis2",
            {
                "enabled": True,
                "status": status,
                "broker": self.broker["host"],
                "topics": self.topics,
                "download_dir": str(self.download_dir),
                "updated_at": _utcnow(),
                **self.stats,
                **extra,
            },
        )

    def handle_message(self, topic: str, payload: bytes) -> Wis2DownloadResult | None:
        self.stats["received"] += 1
        try:
            result = self.downloader.process(topic, payload)
            if result.status in self.stats:
                self.stats[result.status] += 1
            if result.status == "downloaded" and result.path is not None:
                self.workspace.emit_event(
                    "wis2.download",
                    f"WIS2: получен {result.notification.data_id}",
                    details={"path": str(result.path), "topic": topic, "bytes": result.bytes_written},
                )
                if self.enqueue_sync:
                    from ..jobs import JobQueue

                    JobQueue(self.workspace).enqueue(
                        "source.sync",
                        {},
                        priority=20,
                        dedupe_key="wis2:source.sync",
                        max_attempts=int(self.cfg.get("worker", {}).get("max_attempts", 3)),
                    )
            self._state(
                "connected",
                last_message_at=_utcnow(),
                last_topic=topic,
                last_data_id=result.notification.data_id,
                last_result=result.status,
                last_download_path=str(result.path) if result.path else None,
            )
            return result
        except Exception as exc:
            self.stats["errors"] += 1
            self.workspace.emit_event(
                "wis2.error",
                "Ошибка обработки WIS2 notification",
                severity="warning",
                details={"topic": topic, "error": str(exc)},
            )
            self._state("degraded", last_error=str(exc), last_error_at=_utcnow())
            LOGGER.exception("Ошибка WIS2 message на topic %s", topic)
            return None

    def run_forever(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError("Для WIS2 установите SatProf с extra [wis2] или [all]") from exc

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            protocol=mqtt.MQTTv5,
        )
        self._client = client
        if self.broker["username"] is not None:
            client.username_pw_set(self.broker["username"], self.broker["password"])
        if self.broker["tls"]:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
            client.tls_insecure_set(not bool(self.source_cfg.get("verify_tls", True)))
        client.reconnect_delay_set(min_delay=2, max_delay=120)

        def on_connect(_client, _userdata, _flags, reason_code, _properties):
            failed = bool(getattr(reason_code, "is_failure", False))
            if not failed:
                try:
                    failed = int(reason_code) != 0
                except (TypeError, ValueError):
                    failed = False
            if failed:
                self._state("error", last_error=f"MQTT connect reason={reason_code}")
                return
            for topic in self.topics:
                _client.subscribe(topic, qos=self.qos)
            self._state("connected", connected_at=_utcnow())

        def on_disconnect(_client, _userdata, _flags, reason_code, _properties):
            status = "stopped" if self.stop_event.is_set() else "disconnected"
            self._state(status, disconnect_reason=str(reason_code))

        def on_message(_client, _userdata, message):
            self.handle_message(message.topic, bytes(message.payload))

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        self._state("connecting")
        client.connect(
            self.broker["host"],
            self.broker["port"],
            keepalive=int(self.source_cfg.get("keepalive", 60)),
        )
        client.loop_start()
        try:
            while not self.stop_event.wait(1.0):
                pass
        finally:
            try:
                client.disconnect()
                client.loop_stop()
            finally:
                self._state("stopped")

    def stop(self) -> None:
        self.stop_event.set()
