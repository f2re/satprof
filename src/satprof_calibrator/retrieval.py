from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd
import joblib
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.linear_model import RidgeCV
from sklearn.metrics import mean_squared_error
from .physics import interpolate_log_pressure, rh_to_specific_humidity
from .storage import Workspace


@dataclass
class RetrievalFitResult:
    version: str
    metrics: dict
    path: Path


class PCARidgeRetriever:
    def __init__(self, pressure_grid_hpa, cfg: dict):
        self.pressure_grid_hpa=np.asarray(pressure_grid_hpa,float); self.cfg=cfg; self.x_scaler=StandardScaler(); self.t_pca=PCA(n_components=int(cfg.get('pca_temperature_components',6))); self.q_pca=PCA(n_components=int(cfg.get('pca_logq_components',7))); self.t_reg=RidgeCV(alphas=np.asarray(cfg.get('ridge_alphas',[0.1,1,10,100]),float)); self.q_reg=RidgeCV(alphas=np.asarray(cfg.get('ridge_alphas',[0.1,1,10,100]),float)); self.feature_columns=None

    def fit(self, X: pd.DataFrame, temperature_k: np.ndarray, logq: np.ndarray, groups: np.ndarray):
        unique=np.unique(groups); split=max(1,int(len(unique)*0.8)); train_groups=set(unique[:split]); train=np.array([g in train_groups for g in groups]); valid=~train
        if not valid.any(): valid[-max(1,len(valid)//5):]=True; train=~valid
        self.feature_columns=list(X.columns); xs=self.x_scaler.fit_transform(X.iloc[train]); tpc=self.t_pca.fit_transform(temperature_k[train]); qpc=self.q_pca.fit_transform(logq[train]); self.t_reg.fit(xs,tpc); self.q_reg.fit(xs,qpc); pred_t,pred_q=self.predict(X.iloc[valid])
        return {'train_samples':int(train.sum()),'validation_samples':int(valid.sum()),'temperature_rmse_k':float(np.sqrt(mean_squared_error(temperature_k[valid].ravel(),pred_t.ravel()))),'logq_rmse':float(np.sqrt(mean_squared_error(logq[valid].ravel(),pred_q.ravel()))),'temperature_rmse_by_level_k':np.sqrt(np.nanmean((temperature_k[valid]-pred_t)**2,axis=0)).tolist(),'logq_rmse_by_level':np.sqrt(np.nanmean((logq[valid]-pred_q)**2,axis=0)).tolist()}

    def predict(self, X: pd.DataFrame):
        xs=self.x_scaler.transform(X[self.feature_columns]); return self.t_pca.inverse_transform(self.t_reg.predict(xs)), self.q_pca.inverse_transform(self.q_reg.predict(xs))


def build_training_data(workspace: Workspace, instrument: str, corrected_pairs: pd.DataFrame, pressure_grid_hpa):
    pivot=corrected_pairs.pivot_table(index='matchup_id',columns='channel',values='corrected_tb_k',aggfunc='first'); geom=corrected_pairs.groupby('matchup_id').agg(scan=('scan','first'),zenith=('satellite_zenith_deg','first'),tpw=('tpw_mm','first'),sounding_id=('sounding_id','first')); data=pivot.join(geom).dropna(); feature_columns=[c for c in data.columns if c != 'sounding_id']
    with workspace.connect() as db: srows={r['id']:r for r in db.execute('SELECT * FROM soundings').fetchall()}
    temps=[]; logqs=[]; keep=[]; groups=[]
    for matchup_id,row in data.iterrows():
        sid=int(row['sounding_id']); profile=workspace.load_sounding_row(srows[sid]); t=interpolate_log_pressure(profile.pressure_hpa,profile.temperature_k,pressure_grid_hpa); q0=rh_to_specific_humidity(profile.pressure_hpa,profile.temperature_k,profile.relative_humidity_pct); q=interpolate_log_pressure(profile.pressure_hpa,np.log(np.clip(q0,1e-8,None)),pressure_grid_hpa)
        if np.isfinite(t).all() and np.isfinite(q).all(): temps.append(t); logqs.append(q); keep.append(matchup_id); groups.append(sid)
    X=data.loc[keep,feature_columns].copy(); X.columns=X.columns.map(str); return X,np.asarray(temps),np.asarray(logqs),np.asarray(groups)


def train_and_save(workspace: Workspace, instrument: str, corrected_pairs: pd.DataFrame, pressure_grid_hpa, cfg: dict) -> RetrievalFitResult:
    X,t,q,groups=build_training_data(workspace,instrument,corrected_pairs,pressure_grid_hpa); min_soundings=int(cfg.get('min_soundings',80))
    if len(np.unique(groups)) < min_soundings: raise RuntimeError(f'Недостаточно независимых радиозондов: {len(np.unique(groups))} < {min_soundings}')
    model=PCARidgeRetriever(pressure_grid_hpa,cfg); metrics=model.fit(X,t,q,groups); metrics['pressure_grid_hpa']=list(map(float,pressure_grid_hpa)); metrics['feature_columns']=list(model.feature_columns); version=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'); path=workspace.root/'models'/'retrieval'/instrument/version; path.mkdir(parents=True,exist_ok=True); joblib.dump(model,path/'retriever.joblib')
    auto_promote=bool(cfg.get('auto_promote',True)); eligible=metrics['temperature_rmse_k'] <= float(cfg.get('max_temperature_rmse_k',3.0)) and metrics['logq_rmse'] <= float(cfg.get('max_logq_rmse',0.8)); previous=workspace.latest_model('retrieval',instrument,prefer_production=True)
    if previous is not None and previous['status']=='production':
        try:
            previous_metrics=json.loads(previous['metrics_json']); allowed=float(cfg.get('max_rmse_degradation_fraction',0.05)); eligible=eligible and metrics['temperature_rmse_k'] <= float(previous_metrics.get('temperature_rmse_k',metrics['temperature_rmse_k']))*(1+allowed); eligible=eligible and metrics['logq_rmse'] <= float(previous_metrics.get('logq_rmse',metrics['logq_rmse']))*(1+allowed)
        except Exception: pass
    status='production' if auto_promote and eligible else 'candidate'; metrics['status']=status; metrics['promotion_eligible']=bool(eligible); (path/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8'); (path/'STATUS').write_text(status+'\n',encoding='utf-8'); workspace.register_model('retrieval',instrument,version,status,path,metrics); return RetrievalFitResult(version,metrics,path)


class OneDVar:
    """Упрощённый решатель. Производственный оператор должен передавать RTTOV Jacobian."""
    def __init__(self,max_iterations=6,tolerance=1e-3): self.max_iterations=max_iterations; self.tolerance=tolerance
    def solve(self,xb,y,forward_and_jacobian,b_cov,r_cov):
        x=np.asarray(xb,float).copy(); bi=np.linalg.pinv(b_cov); ri=np.linalg.pinv(r_cov)
        for iteration in range(self.max_iterations):
            hx,k=forward_and_jacobian(x); lhs=bi+k.T@ri@k; rhs=k.T@ri@(y-hx)-bi@(x-xb); dx=np.linalg.solve(lhs,rhs); x=x+dx
            if np.linalg.norm(dx)/max(np.linalg.norm(x),1.0) < self.tolerance: break
        posterior=np.linalg.pinv(bi+k.T@ri@k); averaging_kernel=posterior@k.T@ri@k; return {'state':x,'posterior_covariance':posterior,'averaging_kernel':averaging_kernel,'iterations':iteration+1,'residual':y-forward_and_jacobian(x)[0]}
