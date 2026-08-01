from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import math
import zlib

import numpy as np

from .schemas import SatelliteGranule

_DTYPE_MAP: dict[str, np.dtype] = {
    "uint16": np.dtype("<u2"),
    "uint32": np.dtype("<u4"),
    "float32": np.dtype("<f4"),
    "float64": np.dtype("<f8"),
}


class SatDumpLevel1CError(ValueError):
    pass


def _safe_array_path(directory: Path, relative: str) -> Path:
    candidate = (directory / relative).resolve()
    try:
        candidate.relative_to(directory.resolve())
    except ValueError as exc:
        raise SatDumpLevel1CError(f"Путь массива выходит за пределы продукта: {relative}") from exc
    return candidate


def _shape(value: Any) -> tuple[int, ...]:
    try:
        result = tuple(int(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise SatDumpLevel1CError("Некорректная shape массива") from exc
    if not result or any(item < 0 for item in result):
        raise SatDumpLevel1CError(f"Некорректная shape: {result}")
    return result


def _read_array(directory: Path, descriptor: dict[str, Any], name: str) -> np.ndarray:
    dtype_name = str(descriptor.get("dtype", ""))
    if dtype_name not in _DTYPE_MAP:
        raise SatDumpLevel1CError(f"{name}: неподдерживаемый dtype {dtype_name}")
    if str(descriptor.get("endian", "little")).lower() != "little":
        raise SatDumpLevel1CError(f"{name}: поддерживается только little-endian")
    shape = _shape(descriptor.get("shape"))
    expected_items = math.prod(shape)
    dtype = _DTYPE_MAP[dtype_name]
    expected_bytes = expected_items * dtype.itemsize
    announced_bytes = int(descriptor.get("bytes", expected_bytes))
    if announced_bytes != expected_bytes:
        raise SatDumpLevel1CError(
            f"{name}: bytes={announced_bytes}, но shape/dtype требуют {expected_bytes}"
        )
    path = _safe_array_path(directory, str(descriptor.get("path", "")))
    data = path.read_bytes()
    if len(data) != expected_bytes:
        raise SatDumpLevel1CError(
            f"{name}: размер файла {len(data)} байт, ожидалось {expected_bytes}"
        )
    expected_crc = str(descriptor.get("crc32", "")).lower().removeprefix("0x")
    if expected_crc:
        actual_crc = f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
        if actual_crc != expected_crc.zfill(8):
            raise SatDumpLevel1CError(
                f"{name}: CRC32 не совпадает: {actual_crc} != {expected_crc.zfill(8)}"
            )
    return np.frombuffer(data, dtype=dtype).copy().reshape(shape)


def _finite_bounds(latitude: np.ndarray, longitude: np.ndarray) -> list[float] | None:
    mask = np.isfinite(latitude) & np.isfinite(longitude)
    if not np.any(mask):
        return None
    return [
        float(np.min(longitude[mask])),
        float(np.min(latitude[mask])),
        float(np.max(longitude[mask])),
        float(np.max(latitude[mask])),
    ]


def read_satdump_level1c_manifest(
    path: str | Path,
    *,
    instrument: str | None = None,
    satellite: str | None = None,
) -> SatelliteGranule:
    manifest_path = Path(path).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "satprof.level1c-binary/1":
        raise SatDumpLevel1CError(
            f"Неподдерживаемая схема Level-1C: {manifest.get('schema')}"
        )
    directory = manifest_path.parent
    arrays = manifest.get("arrays") or {}
    required = (
        "raw_counts", "brightness_temperature", "latitude", "longitude",
        "observation_time", "scan_position", "satellite_zenith", "quality_flag",
    )
    missing = [name for name in required if name not in arrays]
    if missing:
        raise SatDumpLevel1CError("В манифесте нет массивов: " + ", ".join(missing))

    raw_counts = _read_array(directory, arrays["raw_counts"], "raw_counts")
    brightness_temperature = _read_array(
        directory, arrays["brightness_temperature"], "brightness_temperature"
    ).astype(float)
    latitude = _read_array(directory, arrays["latitude"], "latitude").reshape(-1).astype(float)
    longitude = _read_array(directory, arrays["longitude"], "longitude").reshape(-1).astype(float)
    observation_time = _read_array(
        directory, arrays["observation_time"], "observation_time"
    ).reshape(-1).astype(float)
    scan_position = _read_array(directory, arrays["scan_position"], "scan_position").reshape(-1).astype(float)
    satellite_zenith = _read_array(
        directory, arrays["satellite_zenith"], "satellite_zenith"
    ).reshape(-1).astype(float)
    quality_flag = _read_array(directory, arrays["quality_flag"], "quality_flag").reshape(-1)

    if raw_counts.ndim != 2 or brightness_temperature.shape != raw_counts.shape:
        raise SatDumpLevel1CError("raw_counts и brightness_temperature должны иметь shape [fov, channel]")
    n_fov, n_channels = raw_counts.shape
    for name, value in (
        ("latitude", latitude), ("longitude", longitude),
        ("observation_time", observation_time), ("scan_position", scan_position),
        ("satellite_zenith", satellite_zenith), ("quality_flag", quality_flag),
    ):
        if value.size != n_fov:
            raise SatDumpLevel1CError(f"{name}: получено {value.size}, ожидалось {n_fov}")
    channel_entries = manifest.get("channels") or []
    if len(channel_entries) != n_channels:
        raise SatDumpLevel1CError(
            f"channels: получено {len(channel_entries)}, ожидалось {n_channels}"
        )
    channels = np.asarray([entry.get("id", index + 1) for index, entry in enumerate(channel_entries)])
    longitude = ((longitude + 180.0) % 360.0) - 180.0
    calibration_state = str(manifest.get("calibration_state", "unknown"))
    granule_id = str(
        manifest.get("granule_id")
        or f"{manifest_path.parent.parent.name}-{manifest_path.parent.name}"
    )
    metadata: dict[str, Any] = {
        "source_file": str(manifest_path),
        "source_format": "satdump_level1c_binary",
        "producer": manifest.get("producer", "SatDump"),
        "satdump_instrument": manifest.get("satdump_instrument"),
        "calibration_state": calibration_state,
        "valid_brightness_temperature_fraction": float(
            manifest.get(
                "valid_brightness_temperature_fraction",
                np.isfinite(brightness_temperature).mean(),
            )
        ),
        "quantitative_product": bool(np.isfinite(brightness_temperature).any()),
        "raw_counts_available": True,
        "source_shape": manifest.get("source_shape"),
        "spatial_shape": manifest.get("sampled_shape"),
        "stride": manifest.get("stride", 1),
        "bounds": _finite_bounds(latitude, longitude),
        "quality_flag_masks": manifest.get("quality_flag_masks", {}),
        "channel_metadata": channel_entries,
        "tle": manifest.get("tle"),
    }
    return SatelliteGranule(
        instrument=instrument or str(manifest["instrument"]),
        satellite=satellite or str(manifest.get("satellite", "unknown")),
        granule_id=granule_id,
        channels=channels,
        observation_time_epoch_s=observation_time,
        latitude=latitude,
        longitude=longitude,
        scan_position=scan_position,
        satellite_zenith_deg=satellite_zenith,
        brightness_temperature_k=brightness_temperature,
        raw_counts=raw_counts,
        quality_flag=quality_flag,
        metadata=metadata,
    )
