from __future__ import annotations
from datetime import datetime, timezone
import json
import numpy as np
import pandas as pd


def _robust_sigma(series: pd.Series) -> float:
    x=pd.to_numeric(series,errors='coerce').dropna().to_numpy(float)
    if not len(x): return float('nan')
    return float(1.4826*np.median(np.abs(x-np.median(x))))


def update_statistics(workspace,instrument: str,pairs: pd.DataFrame,cfg: dict | None=None) -> dict:
    cfg=cfg or {}
    if pairs is None or pairs.empty: raise RuntimeError('Нет пар радиация–радиозонд для расчёта статистики')
    value='corrected_innovation_k' if 'corrected_innovation_k' in pairs else 'innovation_k'; df=pairs.copy(); df['date']=pd.to_datetime(df['date']).dt.date.astype(str)
    daily=df.groupby(['date','channel']).agg(n=(value,'size'),independent_soundings=('sounding_id','nunique'),mean_k=(value,'mean'),median_k=(value,'median'),rmse_k=(value,lambda x:float(np.sqrt(np.nanmean(np.square(x))))),robust_sigma_k=(value,_robust_sigma),mean_distance_km=('mean_distance_km','mean'),mean_abs_time_offset_s=('time_offset_s',lambda x:float(np.nanmean(np.abs(x))))).reset_index()
    channel=df.groupby('channel').agg(n=(value,'size'),independent_soundings=('sounding_id','nunique'),mean_k=(value,'mean'),median_k=(value,'median'),rmse_k=(value,lambda x:float(np.sqrt(np.nanmean(np.square(x))))),robust_sigma_k=(value,_robust_sigma),start_date=('date','min'),end_date=('date','max')).reset_index(); alarms=[]; drift_alarm=float(cfg.get('drift_alarm_k',0.8)); sigma_alarm=float(cfg.get('residual_sigma_alarm_k',2.0)); min_daily=int(cfg.get('min_daily_pairs_for_alarm',8))
    for r in daily.itertuples():
        if r.n>=min_daily and abs(float(r.median_k))>=drift_alarm: alarms.append({'severity':'warning','type':'daily_bias','date':r.date,'channel':str(r.channel),'value_k':float(r.median_k)})
        if r.n>=min_daily and np.isfinite(r.robust_sigma_k) and float(r.robust_sigma_k)>=sigma_alarm: alarms.append({'severity':'warning','type':'daily_scatter','date':r.date,'channel':str(r.channel),'value_k':float(r.robust_sigma_k)})
    if 'scan' in df:
        for ch,g in df.groupby('channel'):
            valid=np.isfinite(g['scan'])&np.isfinite(g[value])
            if valid.sum()>=20 and np.nanstd(g.loc[valid,'scan'])>0.05:
                slope=float(np.polyfit(g.loc[valid,'scan'],g.loc[valid,value],1)[0])
                if abs(slope)>=float(cfg.get('scan_slope_alarm_k',0.5)): alarms.append({'severity':'warning','type':'scan_dependence','channel':str(ch),'value_k_per_normalized_scan':slope})
    out_dir=workspace.root/'reports'/'statistics'/instrument; out_dir.mkdir(parents=True,exist_ok=True); daily.to_csv(out_dir/'daily_channel_statistics.csv',index=False); channel.to_csv(out_dir/'channel_summary.csv',index=False); health={'instrument':instrument,'generated_at':datetime.now(timezone.utc).isoformat(),'innovation_field':value,'pairs':int(len(df)),'independent_soundings':int(df['sounding_id'].nunique()),'channels':int(df['channel'].nunique()),'alarms':alarms}; (out_dir/'health.json').write_text(json.dumps(health,ensure_ascii=False,indent=2),encoding='utf-8'); return health
