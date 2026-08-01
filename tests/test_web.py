from fastapi.testclient import TestClient

from satprof_calibrator.web.app import create_app


def test_web_health_monitoring_and_enqueue(tmp_path):
    cfg = {
        "workspace": str(tmp_path / "workspace"),
        "_config_dir": str(tmp_path),
        "satdump": {
            "enabled": False,
            "root": str(tmp_path / "missing"),
            "install_prefix": str(tmp_path / "missing-prefix"),
            "runner": "scripts/astra/run.sh",
            "required_branch": "release/1.2.2",
        },
        "monitoring": {"min_free_gib": 0, "min_free_percent": 0},
        "instruments": {},
        "satellites": [],
    }
    client = TestClient(create_app(cfg=cfg))
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["version"] == "0.5.0"
    assert client.get("/health/live").status_code == 200
    monitoring = client.get("/api/v1/monitoring")
    assert monitoring.status_code == 200
    assert monitoring.json()["schema"] == "satprof.monitoring/1"
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "satprof_up 1" in metrics.text
    response = client.post(
        "/api/v1/jobs",
        json={"job_type": "tle.sync", "payload": {}, "dedupe_key": "test"},
    )
    assert response.status_code == 202
    jobs = client.get("/api/v1/jobs").json()
    assert jobs[0]["job_type"] == "tle.sync"
