from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re

import numpy as np
import xarray as xr
import yaml

from .schemas import SatelliteGranule


def _epoch_seconds(values: Any) -> np.ndarray:
    arr=np.asarray(values)
    if np.issubdtype(arr.dtype,np.datetime64): return arr.astype("datetime64[s]").astype(np.int64).astype(float)
    return arr.astype(float)


def _broadcast_flat(values: Any, size: int, *, default: float = np.nan) -> np.ndarray:
    if values is None: return np.full(size,default,dtype=float)
    arr=np.asarray(values)
    if arr.size==1: return np.full(size,float(arr.reshape(-1)[0]),dtype=float)
    arr=arr.reshape(-1)
    if arr.size!=size: raise ValueError(f"Ожидалось {size} значений, получено {arr.size}")
    return arr


def _parse_time_from_filename(path: Path, mapping: dict[str, Any]) -> float:
    regex=mapping.get("time_from_filename_regex"); match=re.search(regex,path.name) if regex else None
    if not match: raise ValueError("Нет времени в продукте и оно не извлекается из имени файла")
    return datetime.strptime(match.group("date")+match.group("time"),"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()


def _normalise_longitude(lon: np.ndarray) -> np.ndarray: return ((np.asarray(lon,dtype=float)+180.0)%360.0)-180.0


def read_generic_netcdf(path: str | Path, instrument: str, mapping_path: str | Path, satellite: str | None = None) -> SatelliteGranule:
    path=Path(path); mapping=yaml.safe_load(Path(mapping_path).read_text(encoding="utf-8")) or {}; var=mapping["variables"]; ds=xr.open_dataset(path,decode_times=True)
    try:
        tb_da=ds[var["brightness_temperature"]]; channel_da=ds[var["channel"]]; channels=np.asarray(channel_da.values).reshape(-1); channel_dim=mapping.get("channel_dimension") or channel_da.dims[0]
        if channel_dim not in tb_da.dims: raise ValueError(f"Размерность канала {channel_dim} не найдена в TB")
        spatial_dims=[d for d in tb_da.dims if d!=channel_dim]; spatial_shape=tuple(int(tb_da.sizes[d]) for d in spatial_dims); tb=np.asarray(tb_da.transpose(*spatial_dims,channel_dim).values,dtype=float).reshape(-1,len(channels)); scale=float(mapping.get("brightness_temperature_scale",1.0)); offset=float(mapping.get("brightness_temperature_offset",0.0)); tb=tb*scale+offset; n_fov=tb.shape[0]
        def flatten(name: str, default: Any=None):
            key=var.get(name)
            if not key or key not in ds: return default
            return np.asarray(ds[key].values).reshape(-1)
        lat=flatten("latitude"); lon=flatten("longitude")
        if lat is None or lon is None: raise ValueError("Level-1C должен содержать latitude и longitude для каждого поля зрения")
        lat=_broadcast_flat(lat,n_fov); lon=_normalise_longitude(_broadcast_flat(lon,n_fov)); time_values=flatten("time"); time_epoch=np.full(n_fov,_parse_time_from_filename(path,mapping)) if time_values is None else _broadcast_flat(_epoch_seconds(time_values),n_fov); scan_values=flatten("scan_position")
        if scan_values is None:
            if len(spatial_shape)>=2:
                columns=spatial_shape[-1]; scan=np.tile(np.linspace(-1.0,1.0,columns),int(n_fov/columns))
            else: scan=np.linspace(-1.0,1.0,n_fov)
        else:
            scan=_broadcast_flat(scan_values,n_fov); maximum=np.nanmax(np.abs(scan)) if np.isfinite(scan).any() else 1.0
            if maximum>1.0: scan=scan/maximum
        corrected=None; corrected_key=var.get("brightness_temperature_corrected")
        if corrected_key and corrected_key in ds:
            corrected_da=ds[corrected_key]; corrected=np.asarray(corrected_da.transpose(*spatial_dims,channel_dim).values,dtype=float).reshape(tb.shape); corrected=corrected*float(mapping.get("corrected_scale",scale))+float(mapping.get("corrected_offset",offset))
        attributes=mapping.get("attributes",{}); satellite_name=satellite or str(ds.attrs.get(attributes.get("satellite",""),ds.attrs.get("platform","unknown"))); granule_id=str(mapping.get("granule_id") or path.stem); metadata={"source_file":str(path.resolve()),"mapping":str(Path(mapping_path).resolve()),"spatial_shape":list(spatial_shape),"bounds":[float(np.nanmin(lon)),float(np.nanmin(lat)),float(np.nanmax(lon)),float(np.nanmax(lat))],"quantitative_product":True,"calibration_state":"corrected" if corrected is not None else mapping.get("calibration_state","level1c"),"source_format":"netcdf"}; metadata.update(mapping.get("metadata",{}))
        return SatelliteGranule(instrument=instrument,satellite=satellite_name,granule_id=granule_id,channels=channels,observation_time_epoch_s=time_epoch,latitude=lat,longitude=lon,scan_position=scan,satellite_zenith_deg=_broadcast_flat(flatten("satellite_zenith"),n_fov,default=0.0),brightness_temperature_k=tb,calibrated_brightness_temperature_k=corrected,satellite_azimuth_deg=_broadcast_flat(flatten("satellite_azimuth"),n_fov) if flatten("satellite_azimuth") is not None else None,solar_zenith_deg=_broadcast_flat(flatten("solar_zenith"),n_fov) if flatten("solar_zenith") is not None else None,surface_type=_broadcast_flat(flatten("surface_type"),n_fov) if flatten("surface_type") is not None else None,cloud_fraction=_broadcast_flat(flatten("cloud_fraction"),n_fov) if flatten("cloud_fraction") is not None else None,quality_flag=_broadcast_flat(flatten("quality_flag"),n_fov) if flatten("quality_flag") is not None else None,metadata=metadata)
    finally: ds.close()


def _parse_iso_time(value: str | int | float) -> float:
    if isinstance(value,(int,float)): return float(value)
    dt=datetime.fromisoformat(str(value).replace("Z","+00:00")); return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()


def read_geotiff_stack_manifest(path: str | Path, *, instrument: str | None = None, satellite: str | None = None) -> SatelliteGranule:
    try:
        import rasterio
        from rasterio.warp import transform as transform_coordinates
    except ImportError as exc: raise RuntimeError("Для GeoTIFF установите satprof[satdump] или rasterio") from exc
    path=Path(path).resolve(); manifest=json.loads(path.read_text(encoding="utf-8")); channels_cfg=manifest.get("channels") or []
    if not channels_cfg: raise ValueError("В манифесте отсутствует массив channels")
    stride=max(1,int(manifest.get("stride",1))); arrays=[]; channel_ids=[]; first_profile=None; lat=None; lon=None
    for channel in channels_cfg:
        unit=str(channel.get("unit","K")).lower()
        if unit not in {"k","kelvin","brightness_temperature_k"}: raise ValueError(f"Канал {channel.get('id')} имеет единицу {unit}; требуется яркостная температура в K")
        raster_path=Path(channel["path"])
        if not raster_path.is_absolute(): raster_path=(path.parent/raster_path).resolve()
        with rasterio.open(raster_path) as dataset:
            if dataset.count<1: raise ValueError(f"В {raster_path} нет растрового слоя")
            data=dataset.read(int(channel.get("band",1))).astype(float); nodata=dataset.nodata
            if nodata is not None: data[data==nodata]=np.nan
            data=data*float(channel.get("scale",1.0))+float(channel.get("offset",0.0)); profile={"width":dataset.width,"height":dataset.height,"crs":str(dataset.crs),"transform":tuple(dataset.transform)}
            if first_profile is None:
                first_profile=profile; rows,cols=np.mgrid[0:dataset.height:stride,0:dataset.width:stride]; xs,ys=rasterio.transform.xy(dataset.transform,rows,cols,offset="center"); xs_arr=np.asarray(xs).reshape(-1); ys_arr=np.asarray(ys).reshape(-1)
                if dataset.crs and not dataset.crs.is_geographic: lon_values,lat_values=transform_coordinates(dataset.crs,"EPSG:4326",xs_arr.tolist(),ys_arr.tolist()); lon=np.asarray(lon_values,dtype=float); lat=np.asarray(lat_values,dtype=float)
                else: lon=xs_arr.astype(float); lat=ys_arr.astype(float)
            elif profile!=first_profile: raise ValueError("Все GeoTIFF-каналы должны иметь одинаковую сетку, CRS и transform")
            arrays.append(data[::stride,::stride].reshape(-1)); channel_ids.append(channel["id"])
    assert lat is not None and lon is not None and first_profile is not None
    tb=np.column_stack(arrays); n_fov=tb.shape[0]; max_fovs=int(manifest.get("max_fovs",0))
    if max_fovs>0 and n_fov>max_fovs:
        sample=np.linspace(0,n_fov-1,max_fovs).round().astype(int); tb,lat,lon=tb[sample],lat[sample],lon[sample]; n_fov=len(sample)
    lon=_normalise_longitude(lon); observation_time=_parse_iso_time(manifest["observation_time"]); width_sampled=int(np.ceil(first_profile["width"]/stride)); scan=np.tile(np.linspace(-1.0,1.0,width_sampled),int(np.ceil(n_fov/width_sampled)))[:n_fov]; calibration_state=str(manifest.get("calibration_state","calibrated")); corrected=tb.copy() if calibration_state in {"calibrated","corrected","bias_corrected"} else None; metadata={"source_file":str(path),"source_format":"geotiff_stack","quantitative_product":True,"calibration_state":calibration_state,"spatial_shape":[int(np.ceil(first_profile["height"]/stride)),width_sampled],"bounds":[float(np.nanmin(lon)),float(np.nanmin(lat)),float(np.nanmax(lon)),float(np.nanmax(lat))],"crs":first_profile["crs"],"stride":stride}; metadata.update(manifest.get("metadata",{}))
    return SatelliteGranule(instrument=instrument or manifest["instrument"],satellite=satellite or manifest["satellite"],granule_id=str(manifest.get("granule_id") or path.stem),channels=np.asarray(channel_ids),observation_time_epoch_s=np.full(n_fov,observation_time),latitude=lat,longitude=lon,scan_position=scan,satellite_zenith_deg=np.full(n_fov,float(manifest.get("satellite_zenith_deg",0.0))),brightness_temperature_k=tb,calibrated_brightness_temperature_k=corrected,metadata=metadata)
