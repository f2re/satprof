from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
import argparse
import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import load_config, workspace_path
from ..integrations.satdump import SatDumpRunner
from ..jobs import JobQueue
from ..orbit import satellite_geojson
from ..profiles import ProfileRetrievalError, granule_footprint, retrieve_profile
from ..storage import Workspace

ALLOWED_JOB_TYPES={"source.sync","tle.sync","satdump.scan","satdump.process","instrument.refresh","calibration.train","statistics.update"}

class RetrieveRequest(BaseModel):
    latitude: float=Field(ge=-90,le=90); longitude: float=Field(ge=-180,le=180); instrument: str|None=None; granule_db_id: int|None=None; max_distance_km: float|None=Field(default=None,gt=0,le=1000)
class EnqueueRequest(BaseModel):
    job_type: str; payload: dict[str,Any]=Field(default_factory=dict); priority: int=Field(default=100,ge=0,le=1000); dedupe_key: str|None=None
class SatDumpProcessRequest(BaseModel): manifest: str


def _row_to_public(row) -> dict[str,Any]:
    item=dict(row)
    for key in ("qc_json","metrics_json","details_json"):
        if key in item:
            try: item[key[:-5] if key.endswith("_json") else key]=json.loads(item.pop(key) or "{}")
            except Exception: pass
    return item


def create_app(*,workspace: str|Path|None=None,config: str|Path|None=None,cfg: dict[str,Any]|None=None) -> FastAPI:
    cfg=cfg or load_config(config); ws=Workspace(workspace_path(cfg,workspace)); ws.init(); queue=JobQueue(ws); static_dir=Path(__file__).with_name("static"); app=FastAPI(title="SatProf API",version="0.3.0",description="Автоматическая калибровка спутниковых радиаций и восстановление профилей атмосферы"); app.state.workspace=ws; app.state.config=cfg; app.state.queue=queue; app.mount("/static",StaticFiles(directory=static_dir),name="static")
    @app.get("/",include_in_schema=False)
    def index(): return FileResponse(static_dir/"index.html")
    @app.get("/api/v1/health")
    def health():
        satdump=SatDumpRunner(ws,cfg).validate()
        try: import sgp4; sgp4_available=True
        except ImportError: sgp4_available=False
        return {"status":"ok","time":datetime.now(timezone.utc).isoformat(),"version":"0.3.0","workspace":str(ws.root),"counts":ws.counts(),"satdump":satdump,"sgp4_available":sgp4_available}
    @app.get("/api/v1/status")
    def status(): return {"counts":ws.counts(),"jobs":queue.list(limit=30),"events":[_row_to_public(r) for r in ws.recent_events(30)],"models":[_row_to_public(r) for r in ws.model_rows(limit=20)]}
    @app.get("/api/v1/satellites")
    def satellites(): return satellite_geojson(ws,cfg)
    @app.get("/api/v1/granules")
    def granules(instrument: str|None=None,limit: int=Query(default=50,ge=1,le=200)):
        features=[]
        for row in ws.all_granule_rows(instrument=instrument,include_failed=False,limit=limit):
            try:
                granule=ws.load_granule_row(row); properties={"db_id":int(row["id"]),"granule_id":granule.granule_id,"instrument":granule.instrument,"satellite":granule.satellite,"start_time":row["start_time"],"end_time":row["end_time"],"n_fov":int(row["n_fov"]),"n_channels":int(row["n_channels"]),"channels":[str(c) for c in granule.channels],"calibration_state":granule.metadata.get("calibration_state","unknown"),"source_format":granule.metadata.get("source_format","npz")}; features.append({"type":"Feature","geometry":granule_footprint(granule),"properties":properties})
            except Exception as exc: ws.emit_event("web.granule",f"Не удалось построить контур гранулы {row['id']}",severity="warning",details={"error":str(exc)})
        return {"type":"FeatureCollection","features":features}
    @app.get("/api/v1/granules/{granule_id}")
    def granule_details(granule_id: int):
        row=ws.get_granule_row(granule_id)
        if row is None: raise HTTPException(404,"Гранула не найдена")
        granule=ws.load_granule_row(row); return {"catalog":_row_to_public(row),"metadata":granule.metadata,"channels":[str(c) for c in granule.channels],"footprint":granule_footprint(granule)}
    @app.post("/api/v1/profile/retrieve")
    def profile(request: RetrieveRequest):
        try: return retrieve_profile(ws,cfg,latitude=request.latitude,longitude=request.longitude,instrument=request.instrument,granule_db_id=request.granule_db_id,max_distance_km=request.max_distance_km)
        except ProfileRetrievalError as exc: raise HTTPException(404 if exc.code in {"NO_GRANULES","GRANULE_NOT_FOUND"} else 409,{"code":exc.code,"message":str(exc),"details":exc.details}) from exc
    @app.get("/api/v1/jobs")
    def jobs(status: str|None=None,limit: int=Query(default=50,ge=1,le=200)): return queue.list(status=status,limit=limit)
    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: int):
        item=queue.get(job_id)
        if item is None: raise HTTPException(404,"Задание не найдено")
        return item
    @app.post("/api/v1/jobs",status_code=202)
    def enqueue(request: EnqueueRequest):
        if request.job_type not in ALLOWED_JOB_TYPES: raise HTTPException(400,f"Недопустимый тип задания: {request.job_type}")
        return {"job_id":queue.enqueue(request.job_type,request.payload,priority=request.priority,dedupe_key=request.dedupe_key,max_attempts=int(cfg.get("worker",{}).get("max_attempts",3))),"status":"queued"}
    @app.post("/api/v1/satdump/process",status_code=202)
    def process_satdump(request: SatDumpProcessRequest):
        configured=Path(cfg.get("satdump",{}).get("inbox","inbox/satdump")); inbox=configured if configured.is_absolute() else ws.root/configured; manifest=Path(request.manifest).expanduser(); manifest=(inbox/manifest).resolve() if not manifest.is_absolute() else manifest.resolve(); inbox_resolved=inbox.resolve()
        if inbox_resolved not in manifest.parents: raise HTTPException(400,"Манифест должен находиться внутри настроенного SatDump inbox")
        if not manifest.exists(): raise HTTPException(404,"Манифест не найден")
        return {"job_id":queue.enqueue("satdump.process",{"manifest":str(manifest)},priority=30,dedupe_key=f"satdump.process:{manifest}"),"manifest":str(manifest)}
    @app.get("/api/v1/models")
    def models(instrument: str|None=None,model_type: Literal["bias","retrieval"]|None=None): return [_row_to_public(r) for r in ws.model_rows(model_type=model_type,instrument=instrument,limit=100)]
    return app


def main(argv=None):
    import uvicorn
    parser=argparse.ArgumentParser(description="Веб-интерфейс SatProf"); parser.add_argument("--config"); parser.add_argument("--workspace"); parser.add_argument("--host"); parser.add_argument("--port",type=int); args=parser.parse_args(argv); cfg=load_config(args.config); uvicorn.run(create_app(workspace=args.workspace,cfg=cfg),host=args.host or cfg.get("web",{}).get("host","127.0.0.1"),port=args.port or int(cfg.get("web",{}).get("port",8088)))

if __name__=="__main__": main()
