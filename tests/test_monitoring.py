from datetime import datetime, timezone

from satprof_calibrator.monitoring import collect_monitoring, prometheus_metrics
from satprof_calibrator.storage import Workspace


def test_monitoring_report_and_prometheus(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "workspace")
    workspace.init()
    cfg = {
        "_config_dir": str(tmp_path),
        "workspace": str(workspace.root),
        "satdump": {"enabled": False, "root": str(tmp_path / "missing"), "install_prefix": str(tmp_path / "missing-prefix")},
        "monitoring": {"min_free_gib": 0, "min_free_percent": 0},
        "instruments": {},
    }
    report = collect_monitoring(workspace, cfg, deep=False, write_snapshot=True)
    assert report["schema"] == "satprof.monitoring/1"
    assert report["live"] is True
    assert (workspace.root / "monitoring" / "latest.json").is_file()
    metrics = prometheus_metrics(report, workspace)
    assert "satprof_up 1" in metrics
    assert "satprof_ready" in metrics
    assert "satprof_catalog_objects" in metrics
