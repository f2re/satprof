from fastapi.testclient import TestClient
from satprof_calibrator.web.app import create_app


def test_web_health_and_enqueue(tmp_path):
    cfg = {
        "workspace": str(tmp_path / "workspace"),
        "_config_dir": str(tmp_path),
        "satdump": {"root": str(tmp_path / "missing"), "runner": "scripts/astra/run.sh", "required_branch": "release/1.2.2"},
        "satellites": [],
    }
    client = TestClient(create_app(cfg=cfg))
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["version"] == "0.3.0"
    response = client.post("/api/v1/jobs", json={"job_type": "tle.sync", "payload": {}, "dedupe_key": "test"})
    assert response.status_code == 202
    jobs = client.get("/api/v1/jobs").json()
    assert jobs[0]["job_type"] == "tle.sync"
