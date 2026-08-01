from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd
import joblib
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.linear_model import HuberRegressor
from sklearn.metrics import mean_squared_error
from .storage import Workspace

NUMERIC_FEATURES = ['scan','scan2','scan3','secant_zenith_minus_1','sim_tb_k','tpw_mm','month_sin','month_cos']
CATEGORICAL_FEATURES = ['orbit','surface','daynight']


def ensure_bias_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if 'month_sin' not in out or 'month_cos' not in out:
        if 'month' in out:
            month = pd.to_numeric(out['month'], errors='coerce').fillna(1).to_numpy(float)
        elif 'date' in out:
            month = pd.to_datetime(out['date'], errors='coerce').dt.month.fillna(1).to_numpy(float)
        else:
            month = np.ones(len(out), dtype=float)
        out['month_sin'] = np.sin(2*np.pi*month/12.0)
        out['month_cos'] = np.cos(2*np.pi*month/12.0)
    return out


def robust_sigma(x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if not len(x): return np.nan
    return 1.4826 * np.median(np.abs(x - np.median(x)))


def kalman_daily_drift(df: pd.DataFrame, process_variance: float = 0.0004) -> pd.DataFrame:
    daily = df.groupby('date').agg(residual=('residual_after_static_k','median'), n=('residual_after_static_k','size'), sigma=('residual_after_static_k', robust_sigma)).reset_index()
    x = 0.0; p = 1.0
    estimates = []
    for _, row in daily.iterrows():
        p = p + process_variance
        r = max((float(row['sigma']) if np.isfinite(row['sigma']) else 0.5) ** 2 / max(int(row['n']), 1), 0.0025)
        k = p / (p + r)
        x = x + k * (float(row['residual']) - x)
        p = (1-k)*p
        estimates.append((row['date'], x, np.sqrt(p), int(row['n'])))
    return pd.DataFrame(estimates, columns=['date','drift_k','drift_uncertainty_k','n'])


@dataclass
class BiasFitResult:
    version: str
    status: str
    metrics: dict
    path: Path


class BiasCalibrator:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def _pipeline(self):
        pre = ColumnTransformer([
            ('num', StandardScaler(), NUMERIC_FEATURES),
            ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False, drop='first'), CATEGORICAL_FEATURES),
        ])
        model = HuberRegressor(epsilon=float(self.cfg.get('huber_epsilon', 1.35)), max_iter=1000)
        return Pipeline([('pre', pre), ('model', model)])

    def fit(self, workspace: Workspace, instrument: str, pairs: pd.DataFrame) -> BiasFitResult:
        pairs = ensure_bias_features(pairs)
        version = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        model_dir = workspace.root/'models'/'bias'/instrument/version
        model_dir.mkdir(parents=True, exist_ok=True)
        all_metrics = {'channels': {}, 'version': version}
        fitted = {}
        coef_rows = []
        drift_frames = []
        min_pairs = int(self.cfg.get('min_pairs_per_channel', 80))
        min_soundings = int(self.cfg.get('min_independent_soundings', 40))
        valid_fraction = float(self.cfg.get('validation_fraction', 0.2))
        eligible = True
        for channel, g in pairs.groupby('channel'):
            g = g.sort_values('datetime').copy()
            if len(g) < min_pairs or g['sounding_id'].nunique() < min_soundings:
                all_metrics['channels'][channel] = {'n': len(g), 'soundings': int(g['sounding_id'].nunique()), 'status':'insufficient'}
                eligible = False
                continue
            unique_dates = sorted(g['date'].unique())
            cut_idx = max(1, int(len(unique_dates)*(1-valid_fraction)))
            train_dates = set(unique_dates[:cut_idx]); valid_dates = set(unique_dates[cut_idx:])
            train = g[g.date.isin(train_dates)]; valid = g[g.date.isin(valid_dates)]
            if valid.empty:
                valid = train.tail(max(1, len(train)//5)); train = train.iloc[:-len(valid)]
            pipe = self._pipeline()
            sample_weight = 1 / np.square(np.clip(train['uncertainty_k'].to_numpy(float), 0.05, None))
            pipe.fit(train[NUMERIC_FEATURES+CATEGORICAL_FEATURES], train['innovation_k'], model__sample_weight=sample_weight)
            pred_valid = pipe.predict(valid[NUMERIC_FEATURES+CATEGORICAL_FEATURES])
            base_rmse = float(np.sqrt(mean_squared_error(valid['innovation_k'], np.zeros(len(valid)))))
            corr_rmse = float(np.sqrt(mean_squared_error(valid['innovation_k'], pred_valid)))
            residual_bias = float(np.mean(valid['innovation_k']-pred_valid))
            improvement = 1 - corr_rmse/base_rmse if base_rmse else 0.0
            full_pred = pipe.predict(g[NUMERIC_FEATURES+CATEGORICAL_FEATURES])
            g['residual_after_static_k'] = g['innovation_k'] - full_pred
            drift = kalman_daily_drift(g, float(self.cfg.get('kalman_process_variance_k2_per_day', 0.0004)))
            drift['channel'] = channel; drift_frames.append(drift)
            metrics = {
                'n': int(len(g)), 'soundings': int(g.sounding_id.nunique()), 'train_n': int(len(train)), 'validation_n': int(len(valid)),
                'raw_bias_k': float(valid.innovation_k.mean()), 'raw_rmse_k': base_rmse,
                'corrected_bias_k': residual_bias, 'corrected_rmse_k': corr_rmse, 'rmse_improvement_fraction': improvement,
                'residual_robust_sigma_k': float(robust_sigma(valid['innovation_k']-pred_valid)),
            }
            all_metrics['channels'][channel] = metrics
            if abs(residual_bias) > float(self.cfg.get('max_abs_residual_bias_k', 0.2)) or improvement < float(self.cfg.get('min_rmse_improvement_fraction', 0.02)):
                eligible = False
            fitted[channel] = pipe
            pre = pipe.named_steps['pre']; model = pipe.named_steps['model']
            names = pre.get_feature_names_out()
            scaler = pre.named_transformers_['num']
            physical_intercept = float(model.intercept_)
            for name, coef in zip(names, model.coef_):
                physical = float(coef)
                units = 'dummy'
                if name.startswith('num__'):
                    feature = name.split('num__',1)[1]
                    pos = NUMERIC_FEATURES.index(feature)
                    physical = float(coef / scaler.scale_[pos])
                    physical_intercept -= float(coef * scaler.mean_[pos] / scaler.scale_[pos])
                    units = 'K per source unit'
                coef_rows.append({'channel':channel,'feature':name,'coefficient_model_space':float(coef),'coefficient_physical_space':physical,'interpretation':units})
            coef_rows.append({'channel':channel,'feature':'intercept','coefficient_model_space':float(model.intercept_),'coefficient_physical_space':physical_intercept,'interpretation':'K'})
        status = 'production' if eligible and fitted else 'candidate'
        joblib.dump(fitted, model_dir/'bias_models.joblib')
        pd.DataFrame(coef_rows).to_csv(model_dir/'coefficients.csv', index=False)
        if drift_frames:
            pd.concat(drift_frames, ignore_index=True).to_csv(model_dir/'daily_drift.csv', index=False)
        (model_dir/'metrics.json').write_text(json.dumps(all_metrics, ensure_ascii=False, indent=2), encoding='utf-8')
        (model_dir/'STATUS').write_text(status+'\n', encoding='utf-8')
        workspace.register_model('bias', instrument, version, status, model_dir, all_metrics)
        return BiasFitResult(version, status, all_metrics, model_dir)


def apply_bias_model(pairs: pd.DataFrame, model_dir: str | Path) -> pd.DataFrame:
    model_dir = Path(model_dir)
    models = joblib.load(model_dir/'bias_models.joblib')
    drift_path = model_dir/'daily_drift.csv'
    drift = pd.read_csv(drift_path) if drift_path.exists() else pd.DataFrame(columns=['date','channel','drift_k'])
    drift_series = {}
    if not drift.empty:
        drift['date_ts'] = pd.to_datetime(drift['date'], utc=True)
        for ch, g in drift.sort_values('date_ts').groupby('channel'):
            drift_series[str(ch)] = (g['date_ts'].astype('int64').to_numpy(), g['drift_k'].to_numpy(float))
    out = ensure_bias_features(pairs)
    corrections = np.zeros(len(out))
    for channel, idx in out.groupby('channel').groups.items():
        model_key = channel if channel in models else str(channel)
        if model_key not in models:
            try: model_key = int(channel)
            except Exception: pass
        if model_key not in models: continue
        rows = out.loc[idx]
        static = models[model_key].predict(rows[NUMERIC_FEATURES+CATEGORICAL_FEATURES])
        dates = pd.to_datetime(rows['date'], utc=True).astype('int64').to_numpy()
        dyn = np.zeros(len(rows), dtype=float)
        series = drift_series.get(str(channel))
        if series is not None:
            times, values = series
            pos = np.searchsorted(times, dates, side='right') - 1
            valid = pos >= 0
            dyn[valid] = values[pos[valid]]
        corrections[out.index.get_indexer(idx)] = static + dyn
    out['estimated_bias_k'] = corrections
    out['corrected_tb_k'] = out['obs_tb_k'] - out['estimated_bias_k']
    out['corrected_innovation_k'] = out['corrected_tb_k'] - out['sim_tb_k']
    return out
