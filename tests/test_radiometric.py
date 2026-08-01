import numpy as np
import pandas as pd

from satprof_calibrator.radiometric import (
    CountRadiometricCalibrator,
    apply_count_model_to_granule,
)
from satprof_calibrator.schemas import SatelliteGranule
from satprof_calibrator.storage import Workspace


def test_count_to_tb_calibration(tmp_path):
    generator = np.random.default_rng(4)
    rows = []
    for sounding in range(80):
        for scan in (-0.8, -0.2, 0.2, 0.8):
            count = 15000 + 300 * sounding + 500 * scan + generator.normal(0, 30)
            brightness_temperature = 180 + 0.003 * count + 1.5 * scan + generator.normal(0, 0.15)
            rows.append(
                {
                    "sounding_id": sounding,
                    "datetime": f"2026-01-{1 + sounding % 28:02d}T00:00:00+00:00",
                    "channel": "1",
                    "measurement_kind": "raw_counts",
                    "raw_count": count,
                    "sim_tb_k": brightness_temperature,
                    "uncertainty_k": 0.5,
                    "scan": scan,
                    "scan2": scan * scan,
                    "secant_zenith_minus_1": 0.05,
                }
            )
    workspace = Workspace(tmp_path)
    workspace.init()
    fit = CountRadiometricCalibrator(
        {
            "min_pairs_per_channel": 100,
            "min_independent_soundings": 40,
            "max_validation_rmse_k": 1.0,
            "max_abs_validation_bias_k": 0.5,
        }
    ).fit(workspace, "mtvza_gy", pd.DataFrame(rows))
    assert fit.status == "production"

    raw = np.array([[20000], [30000]], dtype=np.uint16)
    granule = SatelliteGranule(
        "mtvza_gy",
        "METEOR",
        "g",
        np.array(["1"]),
        np.array([1.7e9, 1.7e9]),
        np.array([50, 51]),
        np.array([30, 31]),
        np.array([0.0, 0.0]),
        np.array([10.0, 10.0]),
        np.full((2, 1), np.nan),
        raw_counts=raw,
        quality_flag=np.array([4, 4], dtype=np.uint16),
    )
    applied = apply_count_model_to_granule(granule, fit.path)
    assert applied == 2
    assert np.isfinite(granule.brightness_temperature_k).all()
    assert granule.metadata["calibration_state"] == "vicarious_calibrated"
