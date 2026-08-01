import json
import joblib
import numpy as np
from satprof_calibrator.profiles import retrieve_profile
from satprof_calibrator.schemas import SatelliteGranule
from satprof_calibrator.storage import Workspace


class DummyRetriever:
    feature_columns = ["11", "12", "scan", "zenith", "tpw"]
    pressure_grid_hpa = np.array([1000.0, 850.0, 700.0, 500.0, 300.0, 100.0])

    def predict(self, frame):
        temperature = np.array([[289.0, 282.0, 273.0, 255.0, 235.0, 215.0]])
        logq = np.log(np.array([[0.010, 0.007, 0.004, 0.0015, 0.0004, 0.00005]]))
        return temperature, logq


def test_retrieve_profile_from_nearest_fov(tmp_path):
    ws = Workspace(tmp_path / "workspace"); ws.init()
    granule = SatelliteGranule(
        "mtvza_gy", "METEOR", "g1", np.array([11, 12]),
        np.array([1_700_000_000.0, 1_700_000_001.0]), np.array([60.0, 61.0]), np.array([30.0, 31.0]),
        np.array([0.0, 0.2]), np.array([10.0, 15.0]), np.array([[250.0, 251.0], [252.0, 253.0]]),
        calibrated_brightness_temperature_k=np.array([[249.7, 250.8], [251.8, 252.9]]),
        metadata={"calibration_state": "corrected"},
    )
    ws.store_granule(granule, {"passed": True, "flags": [], "metrics": {}})
    model_dir = ws.root / "models" / "retrieval" / "mtvza_gy" / "v1"
    model_dir.mkdir(parents=True)
    joblib.dump(DummyRetriever(), model_dir / "retriever.joblib")
    metrics = {"temperature_rmse_k": 1.2, "logq_rmse": 0.3}
    (model_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    ws.register_model("retrieval", "mtvza_gy", "v1", "production", model_dir, metrics)
    result = retrieve_profile(ws, {"retrieval": {"feature_defaults": {"tpw_mm": 20}}}, latitude=60.05, longitude=30.05, instrument="mtvza_gy")
    assert result["observation"]["fov_index"] == 0
    assert len(result["profile"]) == 6
    assert result["model"]["version"] == "v1"
