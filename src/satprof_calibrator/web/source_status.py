from __future__ import annotations

from typing import Any

from ..storage import Workspace


def source_status(workspace: Workspace, cfg: dict[str, Any]) -> dict[str, Any]:
    with workspace.connect() as db:
        rows = db.execute(
            """SELECT source,
                      COUNT(*) AS files,
                      SUM(CASE WHEN status='ok' THEN records ELSE 0 END) AS records,
                      SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                      MAX(processed_at) AS last_processed_at
                 FROM ingested_files
             GROUP BY source
             ORDER BY source"""
        ).fetchall()
    ingested = {
        str(row["source"]): {
            "files": int(row["files"] or 0),
            "records": int(row["records"] or 0),
            "errors": int(row["errors"] or 0),
            "last_processed_at": row["last_processed_at"],
        }
        for row in rows
    }
    enabled = bool(cfg.get("sources", {}).get("wis2", {}).get("enabled", False))
    default = {"enabled": enabled, "status": "not_started" if enabled else "disabled"}
    return {
        "ingested": ingested,
        "wis2": workspace.get_state("source.wis2", default),
        "last_sounding_sync": workspace.get_state("schedule.last:sync_soundings"),
    }
