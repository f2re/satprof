from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import argparse
import json
import math
import os
import shutil
from . import __version__
from .config import load_config, workspace_path
from .integrations.satdump import SatDumpRunner
from .storage import Workspace

@dataclass
class MonitorCheck:
    name: str
    status: str
    message: str
    critical: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _age_seconds(value: str | None, now: datetime) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())

def _human_age(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return 'нет данных'
    if seconds < 120:
        return f'{int(seconds)} с'
    if seconds < 7200:
        return f'{seconds / 60:.0f} мин'
    if seconds < 172800:
        return f'{seconds / 3600:.1f} ч'
    return f'{seconds / 86400:.1f} сут'

def _scalar(workspace: Workspace, sql: str, params: tuple[Any, ...]=()) -> Any:
    with workspace.connect() as db:
        row = db.execute(sql, params).fetchone()
    return None if row is None else row[0]

def _table_exists(workspace: Workspace, table: str) -> bool:
    return bool(_scalar(workspace, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)))

def _check(checks: list[MonitorCheck], name: str, status: str, message: str, *, critical: bool=False, details: dict[str, Any] | None=None) -> None:
    checks.append(MonitorCheck(name, status, message, critical, details or {}))

def collect_monitoring(workspace: Workspace, cfg: dict[str, Any], *, deep: bool=False, write_snapshot: bool=False) -> dict[str, Any]:
    """Collect readiness, freshness and dependency diagnostics.

    The fast path performs no network requests and no expensive model work.  A
    deep check additionally starts the installed SatDump binary and runs
    SQLite's quick_check.
    """
    workspace.init()
    now = _utcnow()
    monitoring_cfg = cfg.get('monitoring', {})
    checks: list[MonitorCheck] = []
    try:
        usage = shutil.disk_usage(workspace.root)
        free_gib = usage.free / 1024 ** 3
        free_percent = 100.0 * usage.free / max(usage.total, 1)
        min_free_gib = float(monitoring_cfg.get('min_free_gib', 5.0))
        min_free_percent = float(monitoring_cfg.get('min_free_percent', 5.0))
        writable = os.access(workspace.root, os.W_OK)
        status = 'ok'
        if not writable:
            status = 'error'
        elif free_gib < min_free_gib or free_percent < min_free_percent:
            status = 'warning'
        _check(checks, 'workspace', status, f"свободно {free_gib:.1f} ГиБ ({free_percent:.1f}%), запись {('разрешена' if writable else 'запрещена')}", critical=True, details={'path': str(workspace.root), 'writable': writable, 'total_bytes': usage.total, 'used_bytes': usage.used, 'free_bytes': usage.free, 'free_gib': free_gib, 'free_percent': free_percent})
    except OSError as exc:
        _check(checks, 'workspace', 'error', str(exc), critical=True)
    database_details: dict[str, Any] = {'path': str(workspace.db_path)}
    try:
        with workspace.connect() as db:
            result = db.execute('PRAGMA quick_check').fetchone()[0] if deep else db.execute('SELECT 1').fetchone()[0]
            database_details['quick_check'] = result if deep else 'not_requested'
            database_details['journal_mode'] = db.execute('PRAGMA journal_mode').fetchone()[0]
            database_details['page_count'] = int(db.execute('PRAGMA page_count').fetchone()[0])
            database_details['page_size'] = int(db.execute('PRAGMA page_size').fetchone()[0])
        database_details['size_bytes'] = workspace.db_path.stat().st_size if workspace.db_path.exists() else 0
        ok = not deep or str(result).lower() == 'ok'
        _check(checks, 'database', 'ok' if ok else 'error', 'SQLite доступна' if ok else f'SQLite quick_check: {result}', critical=True, details=database_details)
    except Exception as exc:
        _check(checks, 'database', 'error', str(exc), critical=True, details=database_details)
    queue_details: dict[str, Any] = {}
    try:
        with workspace.connect() as db:
            for state in ('queued', 'running', 'done', 'failed'):
                queue_details[state] = int(db.execute('SELECT COUNT(*) FROM jobs WHERE status=?', (state,)).fetchone()[0])
            oldest = db.execute("SELECT created_at FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            queue_details['oldest_queued_at'] = oldest[0] if oldest else None
            oldest_running = db.execute("SELECT locked_at FROM jobs WHERE status='running' ORDER BY locked_at LIMIT 1").fetchone()
            queue_details['oldest_running_at'] = oldest_running[0] if oldest_running else None
            latest_failure = db.execute("SELECT updated_at,job_type,error FROM jobs WHERE status='failed' ORDER BY updated_at DESC LIMIT 1").fetchone()
            if latest_failure:
                queue_details['latest_failure'] = {'updated_at': latest_failure[0], 'job_type': latest_failure[1], 'error': (latest_failure[2] or '')[-1000:]}
        oldest_age = _age_seconds(queue_details['oldest_queued_at'], now)
        running_age = _age_seconds(queue_details['oldest_running_at'], now)
        queue_details['oldest_queued_age_seconds'] = oldest_age
        queue_details['oldest_running_age_seconds'] = running_age
        max_queue_age = 60.0 * float(monitoring_cfg.get('max_queue_age_minutes', 45.0))
        max_running_age = 60.0 * float(monitoring_cfg.get('max_running_age_minutes', cfg.get('worker', {}).get('stale_job_minutes', 60)))
        queue_status = 'ok'
        queue_message = f"в очереди {queue_details['queued']}, выполняется {queue_details['running']}"
        if running_age is not None and running_age > max_running_age:
            queue_status = 'error'
            queue_message += f'; задание выполняется {_human_age(running_age)}'
        elif oldest_age is not None and oldest_age > max_queue_age:
            queue_status = 'warning'
            queue_message += f'; старейшее ожидает {_human_age(oldest_age)}'
        elif queue_details['failed']:
            queue_status = 'warning'
            queue_message += f"; ошибок {queue_details['failed']}"
        _check(checks, 'jobs', queue_status, queue_message, critical=True, details=queue_details)
    except Exception as exc:
        _check(checks, 'jobs', 'error', str(exc), critical=True, details=queue_details)
    freshness: dict[str, Any] = {}
    freshness_specs = (('soundings', 'SELECT MAX(launch_time) FROM soundings WHERE qc_passed=1', float(monitoring_cfg.get('max_sounding_age_hours', 18.0))), ('satellite', 'SELECT MAX(end_time) FROM satellite_granules WHERE qc_passed=1', float(monitoring_cfg.get('max_satellite_age_hours', 8.0))), ('matchups', 'SELECT MAX(created_at) FROM matchups', float(monitoring_cfg.get('max_matchup_age_hours', 48.0))), ('models', 'SELECT MAX(created_at) FROM model_registry', float(monitoring_cfg.get('max_model_age_hours', 24.0 * 14.0))))
    require_data = bool(monitoring_cfg.get('require_fresh_data_for_readiness', False))
    for name, sql, max_hours in freshness_specs:
        try:
            latest = _scalar(workspace, sql)
            age = _age_seconds(latest, now)
            freshness[name] = {'latest_at': latest, 'age_seconds': age, 'max_age_seconds': max_hours * 3600.0}
            if latest is None:
                _check(checks, f'freshness.{name}', 'error' if require_data else 'warning', 'данных пока нет', critical=require_data, details=freshness[name])
            elif age is not None and age > max_hours * 3600.0:
                _check(checks, f'freshness.{name}', 'error' if require_data else 'warning', f'последние данные: {_human_age(age)} назад', critical=require_data, details=freshness[name])
            else:
                _check(checks, f'freshness.{name}', 'ok', f'последние данные: {_human_age(age)} назад', details=freshness[name])
        except Exception as exc:
            _check(checks, f'freshness.{name}', 'error', str(exc), critical=require_data)
    satdump_required = bool(cfg.get('satdump', {}).get('enabled', True))
    try:
        satdump = SatDumpRunner(workspace, cfg).validate(deep=deep)
        satdump_status = 'ok' if satdump.get('ok') else 'error' if satdump_required else 'warning'
        message = 'SatDump готов'
        if satdump.get('errors'):
            message = '; '.join(satdump['errors'])
        elif satdump.get('warnings'):
            message = '; '.join(satdump['warnings'])
            satdump_status = 'warning'
        _check(checks, 'satdump', satdump_status, message, critical=satdump_required, details=satdump)
    except Exception as exc:
        _check(checks, 'satdump', 'error', str(exc), critical=satdump_required)
    rttov_details: dict[str, Any] = {}
    require_rttov = bool(monitoring_cfg.get('require_rttov_for_readiness', False))
    missing_coefficients: list[str] = []
    for instrument, instrument_cfg in cfg.get('instruments', {}).items():
        coefficient = instrument_cfg.get('coefficient_file')
        if not coefficient:
            rttov_details[instrument] = {'path': None, 'exists': False}
            missing_coefficients.append(instrument)
            continue
        path = Path(coefficient).expanduser()
        if not path.is_absolute():
            path = (Path(cfg.get('_config_dir', Path.cwd())) / path).resolve()
        exists = path.is_file() and os.access(path, os.R_OK)
        rttov_details[instrument] = {'path': str(path), 'exists': exists}
        if not exists:
            missing_coefficients.append(instrument)
    if missing_coefficients:
        _check(checks, 'rttov', 'error' if require_rttov else 'warning', 'нет коэффициентов: ' + ', '.join(missing_coefficients), critical=require_rttov, details=rttov_details)
    else:
        _check(checks, 'rttov', 'ok', 'коэффициенты доступны', details=rttov_details)
    errors = [item for item in checks if item.status == 'error']
    warnings = [item for item in checks if item.status == 'warning']
    critical_errors = [item for item in errors if item.critical]
    state = 'error' if critical_errors else 'degraded' if errors or warnings else 'ok'
    ready = not critical_errors
    report = {'schema': 'satprof.monitoring/1', 'version': __version__, 'generated_at': now.isoformat(), 'state': state, 'live': True, 'ready': ready, 'summary': {'checks': len(checks), 'errors': len(errors), 'warnings': len(warnings), 'critical_errors': len(critical_errors)}, 'checks': [item.as_dict() for item in checks], 'freshness': freshness}
    if write_snapshot:
        target = workspace.root / 'monitoring' / 'latest.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, target)
    return report

def _metric_name(value: str) -> str:
    return ''.join((ch if ch.isalnum() or ch == '_' else '_' for ch in value))

def _escape_label(value: Any) -> str:
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')

def prometheus_metrics(report: dict[str, Any], workspace: Workspace) -> str:
    lines = ['# HELP satprof_up SatProf monitoring process is alive.', '# TYPE satprof_up gauge', 'satprof_up 1', '# HELP satprof_ready SatProf is ready to process data.', '# TYPE satprof_ready gauge', f"satprof_ready {(1 if report.get('ready') else 0)}", '# HELP satprof_monitoring_check_status Monitoring check status: ok=1, warning=0.5, error=0.', '# TYPE satprof_monitoring_check_status gauge']
    status_value = {'ok': 1.0, 'warning': 0.5, 'error': 0.0}
    for check in report.get('checks', []):
        name = _escape_label(check.get('name', 'unknown'))
        lines.append(f'''satprof_monitoring_check_status{{check="{name}",critical="{str(bool(check.get('critical'))).lower()}"}} {status_value.get(check.get('status'), 0.0)}''')
        details = check.get('details') or {}
        if check.get('name') == 'workspace':
            lines.extend(['# HELP satprof_workspace_free_bytes Free bytes on workspace filesystem.', '# TYPE satprof_workspace_free_bytes gauge', f"satprof_workspace_free_bytes {int(details.get('free_bytes', 0))}"])
    try:
        counts = workspace.counts()
        lines.extend(['# HELP satprof_catalog_objects Objects stored in the SatProf catalogue.', '# TYPE satprof_catalog_objects gauge'])
        for key, value in counts.items():
            lines.append(f'satprof_catalog_objects{{kind="{_escape_label(_metric_name(key))}"}} {int(value)}')
    except Exception:
        pass
    lines.extend(['# HELP satprof_data_age_seconds Age of the newest object by data kind.', '# TYPE satprof_data_age_seconds gauge'])
    for kind, item in (report.get('freshness') or {}).items():
        age = item.get('age_seconds')
        if age is not None and math.isfinite(float(age)):
            lines.append(f'satprof_data_age_seconds{{kind="{_escape_label(kind)}"}} {float(age):.3f}')
    return '\n'.join(lines) + '\n'

def main(argv: list[str] | None=None) -> int:
    parser = argparse.ArgumentParser(description='Диагностика и мониторинг SatProf')
    parser.add_argument('--config')
    parser.add_argument('--workspace')
    parser.add_argument('--deep', action='store_true', help='Запустить SatDump и SQLite quick_check')
    parser.add_argument('--prometheus', action='store_true')
    parser.add_argument('--write-snapshot', action='store_true')
    parser.add_argument('--allow-degraded', action='store_true')
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    workspace = Workspace(workspace_path(cfg, args.workspace))
    report = collect_monitoring(workspace, cfg, deep=args.deep, write_snapshot=args.write_snapshot)
    if args.prometheus:
        print(prometheus_metrics(report, workspace), end='')
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if report['ready'] and (args.allow_degraded or report['state'] == 'ok'):
        return 0
    return 1
if __name__ == '__main__':
    raise SystemExit(main())
