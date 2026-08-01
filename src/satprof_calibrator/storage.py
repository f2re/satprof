from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import sqlite3

from .schemas import SoundingProfile, SatelliteGranule


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=10000;
CREATE TABLE IF NOT EXISTS soundings (id INTEGER PRIMARY KEY,profile_key TEXT UNIQUE NOT NULL,source TEXT NOT NULL,station_id TEXT NOT NULL,launch_time TEXT NOT NULL,station_lat REAL,station_lon REAL,min_pressure_hpa REAL,max_altitude_m REAL,n_levels INTEGER,qc_passed INTEGER,qc_json TEXT,path TEXT NOT NULL,sha256 TEXT,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_soundings_time ON soundings(launch_time);
CREATE INDEX IF NOT EXISTS idx_soundings_station ON soundings(station_id);
CREATE TABLE IF NOT EXISTS satellite_granules (id INTEGER PRIMARY KEY,granule_key TEXT UNIQUE NOT NULL,instrument TEXT NOT NULL,satellite TEXT NOT NULL,start_time TEXT NOT NULL,end_time TEXT NOT NULL,n_fov INTEGER,n_channels INTEGER,qc_passed INTEGER,qc_json TEXT,path TEXT NOT NULL,sha256 TEXT,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_granules_time ON satellite_granules(start_time,end_time);
CREATE INDEX IF NOT EXISTS idx_granules_instrument ON satellite_granules(instrument);
CREATE TABLE IF NOT EXISTS matchups (id INTEGER PRIMARY KEY,instrument TEXT NOT NULL,sounding_id INTEGER NOT NULL,granule_id INTEGER NOT NULL,fov_index INTEGER NOT NULL,mean_distance_km REAL NOT NULL,max_distance_km REAL NOT NULL,time_offset_s REAL NOT NULL,normalized_score REAL NOT NULL,trajectory_known INTEGER NOT NULL,created_at TEXT NOT NULL,UNIQUE(instrument,sounding_id,granule_id,fov_index),FOREIGN KEY(sounding_id) REFERENCES soundings(id),FOREIGN KEY(granule_id) REFERENCES satellite_granules(id));
CREATE INDEX IF NOT EXISTS idx_matchups_instrument ON matchups(instrument);
CREATE TABLE IF NOT EXISTS ingested_files (id INTEGER PRIMARY KEY,source TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,status TEXT NOT NULL,records INTEGER NOT NULL DEFAULT 0,error TEXT,processed_at TEXT NOT NULL,UNIQUE(source,sha256));
CREATE INDEX IF NOT EXISTS idx_ingested_files_source ON ingested_files(source,processed_at);
CREATE TABLE IF NOT EXISTS model_registry (id INTEGER PRIMARY KEY,model_type TEXT NOT NULL,instrument TEXT NOT NULL,version TEXT NOT NULL,status TEXT NOT NULL,path TEXT NOT NULL,metrics_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(model_type,instrument,version));
CREATE INDEX IF NOT EXISTS idx_models_lookup ON model_registry(model_type,instrument,status,created_at);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY,job_type TEXT NOT NULL,payload_json TEXT NOT NULL,dedupe_key TEXT,status TEXT NOT NULL DEFAULT 'queued',priority INTEGER NOT NULL DEFAULT 100,attempts INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 3,available_at TEXT NOT NULL,locked_at TEXT,worker_id TEXT,progress REAL NOT NULL DEFAULT 0,message TEXT,result_json TEXT,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedupe_active ON jobs(dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status,available_at,priority,id);
CREATE INDEX IF NOT EXISTS idx_jobs_recent ON jobs(updated_at DESC);
CREATE TABLE IF NOT EXISTS scheduler_state (key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tle_catalog (norad_id INTEGER PRIMARY KEY,name TEXT NOT NULL,line1 TEXT NOT NULL,line2 TEXT NOT NULL,source TEXT NOT NULL,epoch_text TEXT,fetched_at TEXT NOT NULL,metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY,kind TEXT NOT NULL,severity TEXT NOT NULL,message TEXT NOT NULL,details_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_recent ON events(created_at DESC);
"""


def utcnow_iso() -> str: return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""): h.update(block)
    return h.hexdigest()


class Workspace:
    def __init__(self,root: str | Path): self.root=Path(root).expanduser().resolve(); self.db_path=self.root/"catalog.sqlite"

    def init(self) -> None:
        for directory in ("soundings","satellite","matchups","models/bias","models/retrieval","reports/latest","reports/statistics","downloads","inbox/satdump","satdump-output","logs/jobs","exports","tle"): (self.root/directory).mkdir(parents=True,exist_ok=True)
        with self.connect() as db: db.executescript(SCHEMA)

    @contextmanager
    def connect(self, *, immediate: bool=False):
        self.root.mkdir(parents=True,exist_ok=True); db=sqlite3.connect(self.db_path,timeout=30); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); db.execute("PRAGMA busy_timeout=10000")
        try:
            if immediate: db.execute("BEGIN IMMEDIATE")
            yield db; db.commit()
        except Exception: db.rollback(); raise
        finally: db.close()

    def store_sounding(self,profile: SoundingProfile,qc: dict[str,Any]) -> int:
        self.init(); safe_time=profile.launch_time.strftime("%Y%m%dT%H%M%SZ"); path=self.root/"soundings"/profile.source/profile.station_id/f"{safe_time}.npz"; profile.save_npz(path); rel=str(path.relative_to(self.root))
        with self.connect() as db:
            db.execute("""INSERT INTO soundings(profile_key,source,station_id,launch_time,station_lat,station_lon,min_pressure_hpa,max_altitude_m,n_levels,qc_passed,qc_json,path,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(profile_key) DO UPDATE SET qc_passed=excluded.qc_passed,qc_json=excluded.qc_json,path=excluded.path,sha256=excluded.sha256""",(profile.id,profile.source,profile.station_id,profile.launch_time.isoformat(),profile.station_lat,profile.station_lon,float(profile.pressure_hpa.min()),float(profile.altitude_m.max()),len(profile.pressure_hpa),int(qc.get("passed",False)),json.dumps(qc,ensure_ascii=False),rel,sha256_file(path),utcnow_iso())); return int(db.execute("SELECT id FROM soundings WHERE profile_key=?",(profile.id,)).fetchone()[0])

    def store_granule(self,granule: SatelliteGranule,qc: dict[str,Any]) -> int:
        self.init(); safe_id="".join(c if c.isalnum() or c in "-_." else "_" for c in granule.granule_id); path=self.root/"satellite"/granule.instrument/f"{safe_id}.npz"; granule.save_npz(path); rel=str(path.relative_to(self.root)); start=datetime.fromtimestamp(float(granule.observation_time_epoch_s.min()),tz=timezone.utc).isoformat(); end=datetime.fromtimestamp(float(granule.observation_time_epoch_s.max()),tz=timezone.utc).isoformat(); key=f"{granule.instrument}:{granule.satellite}:{granule.granule_id}"
        with self.connect() as db:
            db.execute("""INSERT INTO satellite_granules(granule_key,instrument,satellite,start_time,end_time,n_fov,n_channels,qc_passed,qc_json,path,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(granule_key) DO UPDATE SET qc_passed=excluded.qc_passed,qc_json=excluded.qc_json,path=excluded.path,sha256=excluded.sha256,start_time=excluded.start_time,end_time=excluded.end_time,n_fov=excluded.n_fov,n_channels=excluded.n_channels""",(key,granule.instrument,granule.satellite,start,end,granule.brightness_temperature_k.shape[0],granule.brightness_temperature_k.shape[1],int(qc.get("passed",False)),json.dumps(qc,ensure_ascii=False),rel,sha256_file(path),utcnow_iso())); return int(db.execute("SELECT id FROM satellite_granules WHERE granule_key=?",(key,)).fetchone()[0])

    def sounding_rows(self, *, include_failed: bool=False, limit: int | None=None):
        sql="SELECT * FROM soundings"; params=[]
        if not include_failed: sql+=" WHERE qc_passed=1"
        sql+=" ORDER BY launch_time DESC"
        if limit is not None: sql+=" LIMIT ?"; params.append(int(limit))
        with self.connect() as db: return db.execute(sql,params).fetchall()

    def granule_rows(self,instrument: str, *, include_failed: bool=False, limit: int | None=None):
        sql="SELECT * FROM satellite_granules WHERE instrument=?"; params=[instrument]
        if not include_failed: sql+=" AND qc_passed=1"
        sql+=" ORDER BY start_time DESC"
        if limit is not None: sql+=" LIMIT ?"; params.append(int(limit))
        with self.connect() as db: return db.execute(sql,params).fetchall()

    def all_granule_rows(self, *, instrument: str | None=None, include_failed: bool=False, limit: int=100):
        conditions=[]; params=[]
        if instrument: conditions.append("instrument=?"); params.append(instrument)
        if not include_failed: conditions.append("qc_passed=1")
        sql="SELECT * FROM satellite_granules"+(" WHERE "+" AND ".join(conditions) if conditions else "")+" ORDER BY start_time DESC LIMIT ?"; params.append(int(limit))
        with self.connect() as db: return db.execute(sql,params).fetchall()

    def get_granule_row(self,granule_id: int):
        with self.connect() as db: return db.execute("SELECT * FROM satellite_granules WHERE id=?",(int(granule_id),)).fetchone()
    def load_sounding_row(self,row) -> SoundingProfile: return SoundingProfile.load_npz(self.root/row["path"])
    def load_granule_row(self,row) -> SatelliteGranule: return SatelliteGranule.load_npz(self.root/row["path"])

    def input_was_processed(self,source: str,path: str | Path) -> bool:
        path=Path(path); digest=sha256_file(path)
        with self.connect() as db: row=db.execute("SELECT status FROM ingested_files WHERE source=? AND sha256=?",(source,digest)).fetchone()
        return bool(row and row["status"]=="ok")

    def record_input(self,source: str,path: str | Path,status: str,records: int=0,error: str | None=None) -> None:
        path=Path(path); digest=sha256_file(path)
        with self.connect() as db: db.execute("""INSERT INTO ingested_files(source,path,sha256,status,records,error,processed_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(source,sha256) DO UPDATE SET path=excluded.path,status=excluded.status,records=excluded.records,error=excluded.error,processed_at=excluded.processed_at""",(source,str(path),digest,status,int(records),error,utcnow_iso()))

    def register_model(self,model_type: str,instrument: str,version: str,status: str,path: Path,metrics: dict) -> None:
        with self.connect() as db:
            if status=="production": db.execute("UPDATE model_registry SET status='archived' WHERE model_type=? AND instrument=? AND status='production' AND version<>?",(model_type,instrument,version))
            db.execute("""INSERT INTO model_registry(model_type,instrument,version,status,path,metrics_json,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(model_type,instrument,version) DO UPDATE SET status=excluded.status,path=excluded.path,metrics_json=excluded.metrics_json""",(model_type,instrument,version,status,str(path.relative_to(self.root)),json.dumps(metrics,ensure_ascii=False),utcnow_iso()))

    def model_rows(self, *, model_type: str | None=None, instrument: str | None=None, limit: int=100):
        conditions=[]; params=[]
        if model_type: conditions.append("model_type=?"); params.append(model_type)
        if instrument: conditions.append("instrument=?"); params.append(instrument)
        sql="SELECT * FROM model_registry"+(" WHERE "+" AND ".join(conditions) if conditions else "")+" ORDER BY created_at DESC LIMIT ?"; params.append(int(limit))
        with self.connect() as db: return db.execute(sql,params).fetchall()

    def latest_model(self,model_type: str,instrument: str, *, prefer_production: bool=True):
        order="CASE status WHEN 'production' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END," if prefer_production else ""
        with self.connect() as db: return db.execute(f"SELECT * FROM model_registry WHERE model_type=? AND instrument=? ORDER BY {order} created_at DESC LIMIT 1",(model_type,instrument)).fetchone()

    def set_state(self,key: str,value: Any) -> None:
        with self.connect() as db: db.execute("""INSERT INTO scheduler_state(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",(key,json.dumps(value,ensure_ascii=False),utcnow_iso()))
    def get_state(self,key: str,default: Any=None) -> Any:
        with self.connect() as db: row=db.execute("SELECT value_json FROM scheduler_state WHERE key=?",(key,)).fetchone()
        return default if row is None else json.loads(row["value_json"])

    def store_tle(self,norad_id: int,name: str,line1: str,line2: str, *, source: str,epoch_text: str | None=None,metadata: dict | None=None) -> None:
        with self.connect() as db: db.execute("""INSERT INTO tle_catalog(norad_id,name,line1,line2,source,epoch_text,fetched_at,metadata_json) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(norad_id) DO UPDATE SET name=excluded.name,line1=excluded.line1,line2=excluded.line2,source=excluded.source,epoch_text=excluded.epoch_text,fetched_at=excluded.fetched_at,metadata_json=excluded.metadata_json""",(int(norad_id),name,line1.strip(),line2.strip(),source,epoch_text,utcnow_iso(),json.dumps(metadata or {},ensure_ascii=False)))
    def tle_rows(self):
        with self.connect() as db: return db.execute("SELECT * FROM tle_catalog ORDER BY name").fetchall()

    def emit_event(self,kind: str,message: str, *, severity: str="info",details: dict | None=None) -> int:
        with self.connect() as db: return int(db.execute("INSERT INTO events(kind,severity,message,details_json,created_at) VALUES(?,?,?,?,?)",(kind,severity,message,json.dumps(details or {},ensure_ascii=False),utcnow_iso())).lastrowid)
    def recent_events(self,limit: int=50):
        with self.connect() as db: return db.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT ?",(int(limit),)).fetchall()

    def counts(self) -> dict[str,int]:
        tables=("soundings","satellite_granules","matchups","model_registry","jobs"); result={}
        with self.connect() as db:
            for table in tables: result[table]=int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            result["jobs_queued"]=int(db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]); result["jobs_running"]=int(db.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]); result["alarms"]=int(db.execute("SELECT COUNT(*) FROM events WHERE severity IN ('warning','error')").fetchone()[0])
        return result
    def delete_matchups_for_instrument(self,instrument: str) -> None:
        with self.connect() as db: db.execute("DELETE FROM matchups WHERE instrument=?",(instrument,))
    def rows_to_dicts(self,rows: Iterable[sqlite3.Row]) -> list[dict[str,Any]]: return [dict(row) for row in rows]
