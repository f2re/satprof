from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import shutil
import subprocess

from ..config import resolve_path
from ..qc import check_satellite
from ..satellite import read_generic_netcdf, read_geotiff_stack_manifest
from ..storage import Workspace, sha256_file


@dataclass
class SatDumpManifest:
    input_file: Path
    pipeline: str
    input_level: str
    instrument: str
    satellite: str
    samplerate: int | None
    baseband_format: str | None
    extra_args: list[str]
    reader: dict[str, Any]
    output_name: str | None = None

    @classmethod
    def load(cls, path: str | Path, cfg: dict[str, Any]) -> "SatDumpManifest":
        manifest_path = Path(path).expanduser().resolve()
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        input_value = data.get("input_file") or data.get("input")
        if not input_value: raise ValueError("В манифесте SatDump отсутствует input_file")
        input_file = Path(input_value).expanduser()
        if not input_file.is_absolute(): input_file = (manifest_path.parent / input_file).resolve()
        reader = data.get("reader") or {}
        if "type" not in reader: reader["type"] = "netcdf"
        return cls(input_file=input_file,pipeline=str(data["pipeline"]),input_level=str(data.get("input_level", "baseband")),instrument=str(data["instrument"]),satellite=str(data.get("satellite", "unknown")),samplerate=int(data["samplerate"]) if data.get("samplerate") is not None else None,baseband_format=str(data["baseband_format"]) if data.get("baseband_format") else None,extra_args=[str(value) for value in data.get("extra_args", [])],reader=reader,output_name=data.get("output_name"))


class SatDumpRunner:
    def __init__(self, workspace: Workspace, cfg: dict[str, Any]):
        self.workspace = workspace; self.cfg = cfg; self.satdump_cfg = cfg.get("satdump", {})
        self.root = resolve_path(cfg, self.satdump_cfg.get("root", "/opt/SatDump"))
        self.runner = self.root / self.satdump_cfg.get("runner", "scripts/astra/run.sh")
        self.required_branch = str(self.satdump_cfg.get("required_branch", "release/1.2.2"))

    def detect_branch(self) -> str | None:
        head = self.root / ".git" / "HEAD"
        if not head.exists(): return None
        text = head.read_text(encoding="utf-8", errors="replace").strip(); prefix = "ref: refs/heads/"
        return text[len(prefix):] if text.startswith(prefix) else text[:12]

    def validate(self) -> dict[str, Any]:
        errors: list[str] = []; warnings: list[str] = []
        if not self.root.exists(): errors.append(f"Каталог SatDump не найден: {self.root}")
        if not self.runner.exists(): errors.append(f"Сценарий запуска SatDump не найден: {self.runner}")
        branch = self.detect_branch()
        if branch and branch != self.required_branch: errors.append(f"Ожидалась ветка SatDump {self.required_branch}, найдена {branch}")
        if branch is None: warnings.append("Установленная копия SatDump не содержит .git; ветка проверяется только по конфигурации")
        return {"ok": not errors,"root": str(self.root),"runner": str(self.runner),"branch": branch,"required_branch": self.required_branch,"repository": self.satdump_cfg.get("repository"),"errors": errors,"warnings": warnings}

    def build_command(self, manifest: SatDumpManifest, output_dir: Path) -> list[str]:
        command = ["bash",str(self.runner),"--",manifest.pipeline,manifest.input_level,str(manifest.input_file),str(output_dir)]
        if manifest.samplerate is not None: command.extend(["--samplerate", str(manifest.samplerate)])
        if manifest.baseband_format: command.extend(["--baseband_format", manifest.baseband_format])
        command.extend(manifest.extra_args); return command

    def process_manifest(self, manifest_path: str | Path, *, progress=None) -> dict[str, Any]:
        validation = self.validate()
        if not validation["ok"]: raise RuntimeError("; ".join(validation["errors"]))
        manifest_path = Path(manifest_path).expanduser().resolve(); manifest = SatDumpManifest.load(manifest_path, self.cfg)
        if not manifest.input_file.exists(): raise FileNotFoundError(manifest.input_file)
        digest = sha256_file(manifest.input_file); output_name = manifest.output_name or f"{manifest.input_file.stem}-{digest[:10]}"
        output_root = resolve_path(self.cfg, self.satdump_cfg.get("output", "satdump-output"), workspace_relative=True)
        output_dir = output_root / output_name; output_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.workspace.root / "logs" / "jobs" / f"satdump-{output_name}.log"; provenance_path = output_dir / "satprof-provenance.json"
        command = self.build_command(manifest, output_dir)
        if progress: progress(0.05, "Запуск SatDump")
        started = datetime.now(timezone.utc); env = os.environ.copy(); env["SATPROF_WORKSPACE"] = str(self.workspace.root)
        with log_path.open("ab") as log:
            log.write(("\n=== " + started.isoformat() + " ===\n" + " ".join(command) + "\n").encode("utf-8"))
            process = subprocess.run(command,cwd=self.root,stdout=log,stderr=subprocess.STDOUT,timeout=int(self.satdump_cfg.get("timeout_seconds", 7200)),env=env,check=False)
        if process.returncode != 0: raise RuntimeError(f"SatDump завершился с кодом {process.returncode}; журнал: {log_path}")
        if progress: progress(0.72, "Поиск научных продуктов")
        ingested = self.ingest_outputs(manifest, output_dir); finished = datetime.now(timezone.utc)
        provenance = {"schema":"satprof.satdump-run/1","manifest":str(manifest_path),"input_file":str(manifest.input_file),"input_sha256":digest,"pipeline":manifest.pipeline,"input_level":manifest.input_level,"instrument":manifest.instrument,"satellite":manifest.satellite,"command":command,"satdump":validation,"started_at":started.isoformat(),"finished_at":finished.isoformat(),"return_code":process.returncode,"log":str(log_path),"ingested":ingested}
        provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
        done_dir = manifest_path.parent / "processed"; done_dir.mkdir(parents=True, exist_ok=True); shutil.move(str(manifest_path), str(done_dir / manifest_path.name))
        if progress: progress(1.0, "SatDump и импорт завершены")
        return provenance

    def ingest_outputs(self, manifest: SatDumpManifest, output_dir: Path) -> list[dict[str, Any]]:
        reader_type = str(manifest.reader.get("type", "netcdf")); results: list[dict[str, Any]] = []
        if reader_type == "netcdf":
            glob_pattern = manifest.reader.get("glob", "**/*.nc"); mapping_value = manifest.reader.get("mapping")
            if not mapping_value: raise ValueError("Для reader.type=netcdf укажите reader.mapping")
            mapping_path = Path(mapping_value).expanduser()
            if not mapping_path.is_absolute(): mapping_path = (Path(self.cfg.get("_config_dir", Path.cwd())) / mapping_path).resolve()
            files = sorted(output_dir.glob(glob_pattern))
            if not files: raise RuntimeError(f"SatDump завершён, но по шаблону {glob_pattern} не найден Level 1C NetCDF")
            for path in files:
                if self.workspace.input_was_processed("satdump_netcdf", path): results.append({"file":str(path),"status":"skipped","instrument":manifest.instrument}); continue
                granule = read_generic_netcdf(path, manifest.instrument, mapping_path, manifest.satellite); granule.metadata.update({"satdump_output":str(output_dir),"satdump_branch":self.required_branch})
                qc = check_satellite(granule); granule_id = self.workspace.store_granule(granule, {"passed":qc.passed,"flags":qc.flags,"metrics":qc.metrics}); self.workspace.record_input("satdump_netcdf", path, "ok", 1)
                results.append({"file":str(path),"status":"ok","instrument":manifest.instrument,"granule_id":granule_id,"qc_passed":qc.passed})
        elif reader_type == "geotiff_manifest":
            glob_pattern = manifest.reader.get("glob", "**/*.satprof-level1c.json"); files = sorted(output_dir.glob(glob_pattern))
            if not files: raise RuntimeError(f"Не найден манифест стека GeoTIFF: {glob_pattern}")
            for path in files:
                granule = read_geotiff_stack_manifest(path, instrument=manifest.instrument, satellite=manifest.satellite); granule.metadata.update({"satdump_output":str(output_dir),"satdump_branch":self.required_branch})
                qc = check_satellite(granule); granule_id = self.workspace.store_granule(granule, {"passed":qc.passed,"flags":qc.flags,"metrics":qc.metrics}); self.workspace.record_input("satdump_geotiff", path, "ok", 1)
                results.append({"file":str(path),"status":"ok","instrument":manifest.instrument,"granule_id":granule_id,"qc_passed":qc.passed})
        else: raise ValueError(f"Неизвестный reader.type: {reader_type}")
        return results


def discover_manifests(workspace: Workspace, cfg: dict[str, Any]) -> list[Path]:
    satdump_cfg = cfg.get("satdump", {}); inbox = resolve_path(cfg, satdump_cfg.get("inbox", "inbox/satdump"), workspace_relative=True); inbox.mkdir(parents=True, exist_ok=True)
    return sorted(path for path in inbox.glob(satdump_cfg.get("manifest_glob", "*.satprof.json")) if path.is_file())
