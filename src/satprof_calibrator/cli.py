from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .calibration import BiasCalibrator, apply_bias_model
from .collocation import collocate
from .config import load_config, workspace_path
from .demo import generate_demo
from .integrations.satdump import SatDumpRunner
from .jobs import JobQueue
from .operations import ingest_sounding_file, make_rtm, store_profiles, sync_soundings, update_instrument
from .orbit import sync_tles
from .pairs import build_pairs
from .profiles import ProfileRetrievalError, retrieve_profile
from .qc import check_satellite
from .reporting import generate_report
from .retrieval import train_and_save
from .satellite import read_generic_netcdf, read_geotiff_stack_manifest
from .sources.gruan import read_gruan_netcdf
from .sources.igra import read_igra_file
from .sources.dwd_bufr import read_bufr
from .sources.roshydromet import read_csv as read_roshydromet_csv
from .statistics import update_statistics
from .storage import Workspace
from .wrfda import export_radiances


def _common(parser: argparse.ArgumentParser, *, require_config: bool = False) -> None:
    parser.add_argument("--workspace")
    parser.add_argument("--config", required=require_config)


def _workspace(args) -> tuple[Workspace, dict]:
    cfg = load_config(getattr(args, "config", None))
    ws = Workspace(workspace_path(cfg, getattr(args, "workspace", None)))
    ws.init()
    return ws, cfg


def main(argv=None):
    parser = argparse.ArgumentParser(prog="satprof", description="Калибровка спутниковых радиаций и восстановление профилей")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="Создать рабочий каталог"); _common(p)
    p = sub.add_parser("sync-soundings", help="Загрузить и разобрать настроенные источники зондирования"); _common(p, require_config=True)
    for name in ("ingest-igra", "ingest-dwd-bufr", "ingest-gruan", "ingest-roshydromet"):
        p = sub.add_parser(name); _common(p); p.add_argument("--file", required=True)
    p = sub.add_parser("ingest-satellite"); _common(p); p.add_argument("--file", required=True); p.add_argument("--instrument", required=True); p.add_argument("--mapping", required=True); p.add_argument("--satellite")
    p = sub.add_parser("ingest-geotiff-stack"); _common(p); p.add_argument("--manifest", required=True); p.add_argument("--instrument"); p.add_argument("--satellite")
    p = sub.add_parser("collocate"); _common(p, require_config=True); p.add_argument("--instrument", required=True)
    p = sub.add_parser("build-pairs"); _common(p, require_config=True); p.add_argument("--instrument", required=True)
    p = sub.add_parser("refresh-instrument"); _common(p, require_config=True); p.add_argument("--instrument", required=True); p.add_argument("--fit-bias", action="store_true"); p.add_argument("--train-retrieval", action="store_true")
    p = sub.add_parser("fit-bias"); _common(p, require_config=True); p.add_argument("--instrument", required=True)
    p = sub.add_parser("train-retrieval"); _common(p, require_config=True); p.add_argument("--instrument", required=True); p.add_argument("--model-dir", required=True)
    p = sub.add_parser("update-stats"); _common(p); p.add_argument("--instrument", required=True)
    p = sub.add_parser("export-wrfda"); _common(p); p.add_argument("--instrument", required=True); p.add_argument("--corrected-pairs", required=True); p.add_argument("--calibration-version", required=True); p.add_argument("--output", required=True)
    p = sub.add_parser("report"); _common(p); p.add_argument("--instrument", required=True)
    p = sub.add_parser("satdump-validate"); _common(p, require_config=True)
    p = sub.add_parser("satdump-process"); _common(p, require_config=True); p.add_argument("--manifest", required=True)
    p = sub.add_parser("sync-tle"); _common(p, require_config=True)
    p = sub.add_parser("enqueue"); _common(p); p.add_argument("job_type"); p.add_argument("--payload", default="{}"); p.add_argument("--priority", type=int, default=100); p.add_argument("--dedupe-key")
    p = sub.add_parser("jobs"); _common(p); p.add_argument("--status"); p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("retrieve-profile"); _common(p); p.add_argument("--lat", type=float, required=True); p.add_argument("--lon", type=float, required=True); p.add_argument("--instrument"); p.add_argument("--granule-id", type=int); p.add_argument("--max-distance-km", type=float)
    p = sub.add_parser("demo"); _common(p); p.add_argument("--soundings", type=int, default=120); p.add_argument("--seed", type=int, default=42)

    args = parser.parse_args(argv)
    ws, cfg = _workspace(args)
    qc_cfg = cfg.get("quality_control", {})

    if args.cmd == "init":
        print(ws.root); return
    if args.cmd == "sync-soundings":
        print(json.dumps(sync_soundings(ws, cfg), ensure_ascii=False, indent=2)); return
    if args.cmd == "ingest-igra":
        print(json.dumps(ingest_sounding_file(ws, "igra2", args.file, read_igra_file, qc_cfg), ensure_ascii=False)); return
    if args.cmd == "ingest-dwd-bufr":
        print(json.dumps(ingest_sounding_file(ws, "dwd_bufr", args.file, read_bufr, qc_cfg), ensure_ascii=False)); return
    if args.cmd == "ingest-gruan":
        print(store_profiles(ws, [read_gruan_netcdf(args.file)], qc_cfg)); return
    if args.cmd == "ingest-roshydromet":
        print(store_profiles(ws, read_roshydromet_csv(args.file), qc_cfg)); return
    if args.cmd == "ingest-satellite":
        granule = read_generic_netcdf(args.file, args.instrument, args.mapping, args.satellite)
        qc = check_satellite(granule)
        print(ws.store_granule(granule, {"passed": qc.passed, "flags": qc.flags, "metrics": qc.metrics})); return
    if args.cmd == "ingest-geotiff-stack":
        granule = read_geotiff_stack_manifest(args.manifest, instrument=args.instrument, satellite=args.satellite)
        qc = check_satellite(granule)
        print(ws.store_granule(granule, {"passed": qc.passed, "flags": qc.flags, "metrics": qc.metrics})); return
    if args.cmd == "collocate":
        print(len(collocate(ws, args.instrument, cfg["instruments"][args.instrument], cfg.get("collocation", {})))); return
    if args.cmd == "build-pairs":
        print(len(build_pairs(ws, args.instrument, make_rtm(cfg, args.instrument), cfg["instruments"][args.instrument]))); return
    if args.cmd == "refresh-instrument":
        print(json.dumps(update_instrument(ws, cfg, args.instrument, fit_bias=args.fit_bias, train_retrieval_model=args.train_retrieval), ensure_ascii=False, indent=2)); return
    if args.cmd == "fit-bias":
        pairs = pd.read_csv(ws.root / "matchups" / f"{args.instrument}_radiance_pairs.csv.gz")
        result = BiasCalibrator(cfg.get("calibration", {})).fit(ws, args.instrument, pairs)
        print(json.dumps({"version": result.version, "status": result.status, "path": str(result.path)}, ensure_ascii=False)); return
    if args.cmd == "train-retrieval":
        pairs = apply_bias_model(pd.read_csv(ws.root / "matchups" / f"{args.instrument}_radiance_pairs.csv.gz"), args.model_dir)
        result = train_and_save(ws, args.instrument, pairs, cfg["pressure_grid_hpa"], cfg.get("retrieval", {}))
        print(result.path); return
    if args.cmd == "update-stats":
        corrected = ws.root / "matchups" / f"{args.instrument}_corrected_pairs.csv.gz"
        raw = ws.root / "matchups" / f"{args.instrument}_radiance_pairs.csv.gz"
        print(json.dumps(update_statistics(ws, args.instrument, pd.read_csv(corrected if corrected.exists() else raw), cfg.get("calibration", {})), ensure_ascii=False, indent=2)); return
    if args.cmd == "export-wrfda":
        export_radiances(pd.read_csv(args.corrected_pairs), args.output, args.instrument, args.calibration_version); print(args.output); return
    if args.cmd == "report":
        raw_path = ws.root / "matchups" / f"{args.instrument}_radiance_pairs.csv.gz"
        corrected_path = ws.root / "matchups" / f"{args.instrument}_corrected_pairs.csv.gz"
        print(generate_report(ws, args.instrument, pd.read_csv(raw_path) if raw_path.exists() else None, pd.read_csv(corrected_path) if corrected_path.exists() else None)); return
    if args.cmd == "satdump-validate":
        print(json.dumps(SatDumpRunner(ws, cfg).validate(), ensure_ascii=False, indent=2)); return
    if args.cmd == "satdump-process":
        print(json.dumps(SatDumpRunner(ws, cfg).process_manifest(args.manifest), ensure_ascii=False, indent=2)); return
    if args.cmd == "sync-tle":
        print(json.dumps(sync_tles(ws, cfg), ensure_ascii=False, indent=2)); return
    if args.cmd == "enqueue":
        print(JobQueue(ws).enqueue(args.job_type, json.loads(args.payload), priority=args.priority, dedupe_key=args.dedupe_key)); return
    if args.cmd == "jobs":
        print(json.dumps(JobQueue(ws).list(status=args.status, limit=args.limit), ensure_ascii=False, indent=2)); return
    if args.cmd == "retrieve-profile":
        try:
            result = retrieve_profile(ws, cfg, latitude=args.lat, longitude=args.lon, instrument=args.instrument, granule_db_id=args.granule_id, max_distance_km=args.max_distance_km)
        except ProfileRetrievalError as exc:
            raise SystemExit(f"{exc.code}: {exc}") from exc
        print(json.dumps(result, ensure_ascii=False, indent=2)); return
    if args.cmd == "demo":
        result = generate_demo(ws, args.soundings, args.seed)
        print(json.dumps({"collocations": result["collocations"], "pairs": result["pairs"], "bias_status": result["bias"].status, "report": str(result["report"])}, ensure_ascii=False, indent=2)); return


if __name__ == "__main__":
    main()
