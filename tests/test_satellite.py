import numpy as np
import xarray as xr
from satprof_calibrator.satellite import read_generic_netcdf


def test_read_generic_netcdf_preserves_shape_and_corrected_tb(tmp_path):
    path = tmp_path / "l1c.nc"
    ds = xr.Dataset(
        {
            "tb": (("scan", "pixel", "channel"), np.full((2, 3, 2), 250.0)),
            "tb_corr": (("scan", "pixel", "channel"), np.full((2, 3, 2), 249.5)),
            "lat": (("scan", "pixel"), np.arange(6).reshape(2, 3) + 50.0),
            "lon": (("scan", "pixel"), np.arange(6).reshape(2, 3) + 30.0),
            "time": (("scan", "pixel"), np.full((2, 3), 1_700_000_000.0)),
            "channel": (("channel",), [11, 12]),
        }
    )
    ds.to_netcdf(path)
    mapping = tmp_path / "map.yaml"
    mapping.write_text(
        """variables:\n  brightness_temperature: tb\n  brightness_temperature_corrected: tb_corr\n  latitude: lat\n  longitude: lon\n  time: time\n  channel: channel\n""",
        encoding="utf-8",
    )
    granule = read_generic_netcdf(path, "mtvza_gy", mapping, "METEOR")
    assert granule.brightness_temperature_k.shape == (6, 2)
    assert granule.calibrated_brightness_temperature_k.shape == (6, 2)
    assert granule.metadata["spatial_shape"] == [2, 3]
