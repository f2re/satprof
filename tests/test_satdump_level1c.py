import json
import zlib

import numpy as np
import pytest

from satprof_calibrator.satdump_level1c import (
    SatDumpLevel1CError,
    read_satdump_level1c_manifest,
)
from satprof_calibrator.schemas import SatelliteGranule

DTYPES = {
    "uint16": np.dtype("<u2"),
    "uint32": np.dtype("<u4"),
    "float32": np.dtype("<f4"),
    "float64": np.dtype("<f8"),
}


def _write(root, name, dtype, shape, values, units="1"):
    array = np.asarray(values, dtype=DTYPES[dtype]).reshape(shape)
    data = array.tobytes()
    (root / name).write_bytes(data)
    return {
        "path": name,
        "dtype": dtype,
        "endian": "little",
        "shape": list(shape),
        "units": units,
        "bytes": len(data),
        "crc32": f"{zlib.crc32(data) & 0xFFFFFFFF:08x}",
    }


def _product(tmp_path):
    n_fov, n_channels = 4, 2
    arrays = {
        "raw_counts": _write(tmp_path, "raw.u16", "uint16", (n_fov, n_channels), np.arange(8)),
        "brightness_temperature": _write(tmp_path, "tb.f32", "float32", (n_fov, n_channels), [np.nan] * 8, "K"),
        "latitude": _write(tmp_path, "lat.f32", "float32", (n_fov,), [50, 51, 52, 53]),
        "longitude": _write(tmp_path, "lon.f32", "float32", (n_fov,), [30, 31, 32, 33]),
        "observation_time": _write(tmp_path, "time.f64", "float64", (n_fov,), [1.7e9] * n_fov),
        "scan_position": _write(tmp_path, "scan.f32", "float32", (n_fov,), [-1, -0.3, 0.3, 1]),
        "satellite_zenith": _write(tmp_path, "zen.f32", "float32", (n_fov,), [0, 10, 20, 30]),
        "quality_flag": _write(tmp_path, "qc.u16", "uint16", (n_fov,), [4] * n_fov),
    }
    manifest = {
        "schema": "satprof.level1c-binary/1",
        "instrument": "mtvza_gy",
        "satellite": "METEOR-M2-4",
        "calibration_state": "raw_counts",
        "channels": [{"id": "1"}, {"id": "2"}],
        "arrays": arrays,
        "sampled_shape": [2, 2],
    }
    path = tmp_path / "satprof-level1c.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_read_raw_count_product(tmp_path):
    granule = read_satdump_level1c_manifest(_product(tmp_path))
    assert granule.raw_counts.shape == (4, 2)
    assert np.isnan(granule.brightness_temperature_k).all()
    assert granule.metadata["calibration_state"] == "raw_counts"
    stored = tmp_path / "granule.npz"
    granule.save_npz(stored)
    loaded = SatelliteGranule.load_npz(stored)
    assert np.array_equal(loaded.raw_counts, granule.raw_counts)


def test_crc_is_enforced(tmp_path):
    path = _product(tmp_path)
    (tmp_path / "raw.u16").write_bytes(b"broken")
    with pytest.raises(SatDumpLevel1CError):
        read_satdump_level1c_manifest(path)
