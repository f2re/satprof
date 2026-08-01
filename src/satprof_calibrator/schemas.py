from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import numpy as np


def _arr(value, dtype=float) -> np.ndarray:
    return np.asarray(value, dtype=dtype)


@dataclass
class SoundingProfile:
    source: str
    station_id: str
    launch_time: datetime
    station_lat: float
    station_lon: float
    pressure_hpa: np.ndarray
    temperature_k: np.ndarray
    relative_humidity_pct: np.ndarray
    altitude_m: np.ndarray
    wind_u_ms: np.ndarray
    wind_v_ms: np.ndarray
    elapsed_s: np.ndarray
    latitude: np.ndarray | None = None
    longitude: np.ndarray | None = None
    temperature_uncertainty_k: np.ndarray | None = None
    humidity_uncertainty_pct: np.ndarray | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.launch_time.tzinfo is None:
            self.launch_time = self.launch_time.replace(tzinfo=timezone.utc)
        for name in (
            "pressure_hpa", "temperature_k", "relative_humidity_pct", "altitude_m",
            "wind_u_ms", "wind_v_ms", "elapsed_s",
        ):
            setattr(self, name, _arr(getattr(self, name)))
        n = len(self.pressure_hpa)
        for name in (
            "temperature_k", "relative_humidity_pct", "altitude_m",
            "wind_u_ms", "wind_v_ms", "elapsed_s",
        ):
            if len(getattr(self, name)) != n:
                raise ValueError(f"Несогласованная длина массива {name}")
        for name in ("latitude", "longitude", "temperature_uncertainty_k", "humidity_uncertainty_pct"):
            if getattr(self, name) is not None:
                setattr(self, name, _arr(getattr(self, name)))

    @property
    def id(self) -> str:
        return f"{self.source}:{self.station_id}:{self.launch_time.isoformat()}"

    @property
    def level_times_epoch_s(self) -> np.ndarray:
        return self.launch_time.timestamp() + np.nan_to_num(self.elapsed_s, nan=0.0)

    def save_npz(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        optional = lambda value: np.array([]) if value is None else value
        np.savez_compressed(
            path,
            source=self.source,
            station_id=self.station_id,
            launch_time=self.launch_time.isoformat(),
            station_lat=self.station_lat,
            station_lon=self.station_lon,
            pressure_hpa=self.pressure_hpa,
            temperature_k=self.temperature_k,
            relative_humidity_pct=self.relative_humidity_pct,
            altitude_m=self.altitude_m,
            wind_u_ms=self.wind_u_ms,
            wind_v_ms=self.wind_v_ms,
            elapsed_s=self.elapsed_s,
            latitude=optional(self.latitude),
            longitude=optional(self.longitude),
            temperature_uncertainty_k=optional(self.temperature_uncertainty_k),
            humidity_uncertainty_pct=optional(self.humidity_uncertainty_pct),
            metadata_json=json.dumps(self.metadata, ensure_ascii=False),
        )

    @classmethod
    def load_npz(cls, path: str | Path) -> "SoundingProfile":
        with np.load(path, allow_pickle=False) as data:
            optional = lambda name: None if data[name].size == 0 else data[name]
            return cls(
                source=str(data["source"]),
                station_id=str(data["station_id"]),
                launch_time=datetime.fromisoformat(str(data["launch_time"])),
                station_lat=float(data["station_lat"]),
                station_lon=float(data["station_lon"]),
                pressure_hpa=data["pressure_hpa"],
                temperature_k=data["temperature_k"],
                relative_humidity_pct=data["relative_humidity_pct"],
                altitude_m=data["altitude_m"],
                wind_u_ms=data["wind_u_ms"],
                wind_v_ms=data["wind_v_ms"],
                elapsed_s=data["elapsed_s"],
                latitude=optional("latitude"),
                longitude=optional("longitude"),
                temperature_uncertainty_k=optional("temperature_uncertainty_k"),
                humidity_uncertainty_pct=optional("humidity_uncertainty_pct"),
                metadata=json.loads(str(data["metadata_json"])),
            )


@dataclass
class SatelliteGranule:
    instrument: str
    satellite: str
    granule_id: str
    channels: np.ndarray
    observation_time_epoch_s: np.ndarray
    latitude: np.ndarray
    longitude: np.ndarray
    scan_position: np.ndarray
    satellite_zenith_deg: np.ndarray
    brightness_temperature_k: np.ndarray
    raw_counts: np.ndarray | None = None
    calibrated_brightness_temperature_k: np.ndarray | None = None
    satellite_azimuth_deg: np.ndarray | None = None
    solar_zenith_deg: np.ndarray | None = None
    surface_type: np.ndarray | None = None
    cloud_fraction: np.ndarray | None = None
    quality_flag: np.ndarray | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.channels = np.asarray(self.channels)
        for name in (
            "observation_time_epoch_s", "latitude", "longitude", "scan_position",
            "satellite_zenith_deg", "brightness_temperature_k",
        ):
            setattr(self, name, _arr(getattr(self, name)))
        if self.raw_counts is not None:
            self.raw_counts = np.asarray(self.raw_counts)
        if self.calibrated_brightness_temperature_k is not None:
            self.calibrated_brightness_temperature_k = _arr(self.calibrated_brightness_temperature_k)
        if self.brightness_temperature_k.ndim != 2:
            raise ValueError("brightness_temperature_k должен иметь размерность [fov, channel]")
        n_fov, n_channels = self.brightness_temperature_k.shape
        for name in (
            "observation_time_epoch_s", "latitude", "longitude",
            "scan_position", "satellite_zenith_deg",
        ):
            if len(getattr(self, name)) != n_fov:
                raise ValueError(f"Несогласованная длина {name}")
        if len(self.channels) != n_channels:
            raise ValueError("Число каналов не совпадает с размером TB")
        if self.raw_counts is not None and self.raw_counts.shape != self.brightness_temperature_k.shape:
            raise ValueError("raw_counts должны совпадать по размерности с TB")
        if (
            self.calibrated_brightness_temperature_k is not None
            and self.calibrated_brightness_temperature_k.shape != self.brightness_temperature_k.shape
        ):
            raise ValueError("Калиброванные TB должны совпадать по размерности с исходными")
        for name in (
            "satellite_azimuth_deg", "solar_zenith_deg", "surface_type",
            "cloud_fraction", "quality_flag",
        ):
            value = getattr(self, name)
            if value is not None:
                value = np.asarray(value)
                if value.reshape(-1).size != n_fov:
                    raise ValueError(f"Несогласованная длина {name}")
                setattr(self, name, value.reshape(-1))

    def save_npz(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        optional = lambda value: np.array([]) if value is None else np.asarray(value)
        np.savez_compressed(
            path,
            instrument=self.instrument,
            satellite=self.satellite,
            granule_id=self.granule_id,
            channels=self.channels,
            observation_time_epoch_s=self.observation_time_epoch_s,
            latitude=self.latitude,
            longitude=self.longitude,
            scan_position=self.scan_position,
            satellite_zenith_deg=self.satellite_zenith_deg,
            brightness_temperature_k=self.brightness_temperature_k,
            raw_counts=optional(self.raw_counts),
            calibrated_brightness_temperature_k=optional(self.calibrated_brightness_temperature_k),
            satellite_azimuth_deg=optional(self.satellite_azimuth_deg),
            solar_zenith_deg=optional(self.solar_zenith_deg),
            surface_type=optional(self.surface_type),
            cloud_fraction=optional(self.cloud_fraction),
            quality_flag=optional(self.quality_flag),
            metadata_json=json.dumps(self.metadata, ensure_ascii=False),
        )

    @classmethod
    def load_npz(cls, path: str | Path) -> "SatelliteGranule":
        with np.load(path, allow_pickle=False) as data:
            optional = lambda name: None if name not in data.files or data[name].size == 0 else data[name]
            return cls(
                instrument=str(data["instrument"]),
                satellite=str(data["satellite"]),
                granule_id=str(data["granule_id"]),
                channels=data["channels"],
                observation_time_epoch_s=data["observation_time_epoch_s"],
                latitude=data["latitude"],
                longitude=data["longitude"],
                scan_position=data["scan_position"],
                satellite_zenith_deg=data["satellite_zenith_deg"],
                brightness_temperature_k=data["brightness_temperature_k"],
                raw_counts=optional("raw_counts"),
                calibrated_brightness_temperature_k=optional("calibrated_brightness_temperature_k"),
                satellite_azimuth_deg=optional("satellite_azimuth_deg"),
                solar_zenith_deg=optional("solar_zenith_deg"),
                surface_type=optional("surface_type"),
                cloud_fraction=optional("cloud_fraction"),
                quality_flag=optional("quality_flag"),
                metadata=json.loads(str(data["metadata_json"])),
            )
