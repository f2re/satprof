from __future__ import annotations

import argparse
import logging
import signal

from .wis2_download import Wis2Downloader, Wis2DownloadResult
from .wis2_message import (
    Wis2Error,
    Wis2Notification,
    decode_inline_content,
    notification_filename,
    parse_broker_url,
    parse_notification,
    verify_integrity,
)
from .wis2_subscriber import Wis2Subscriber

__all__ = [
    "Wis2Downloader",
    "Wis2DownloadResult",
    "Wis2Error",
    "Wis2Notification",
    "Wis2Subscriber",
    "decode_inline_content",
    "notification_filename",
    "parse_broker_url",
    "parse_notification",
    "verify_integrity",
]


def main(argv: list[str] | None = None) -> None:
    from ..config import load_config, workspace_path
    from ..storage import Workspace

    parser = argparse.ArgumentParser(description="Подписка SatProf на оперативные TEMP из WIS 2.0")
    parser.add_argument("--config")
    parser.add_argument("--workspace")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config(args.config)
    if not cfg.get("sources", {}).get("wis2", {}).get("enabled", False):
        raise SystemExit("WIS2 отключён: установите sources.wis2.enabled: true")
    workspace = Workspace(workspace_path(cfg, args.workspace))
    workspace.init()
    subscriber = Wis2Subscriber(workspace, cfg)
    signal.signal(signal.SIGTERM, lambda *_: subscriber.stop())
    signal.signal(signal.SIGINT, lambda *_: subscriber.stop())
    subscriber.run_forever()


if __name__ == "__main__":
    main()
