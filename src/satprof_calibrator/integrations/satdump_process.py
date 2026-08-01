from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
import fcntl
import json
import os
import shutil
import subprocess
import uuid

from ..config import resolve_path
from ..qc import check_satellite
from ..satellite import read_generic_netcdf, read_geotiff_stack_manifest
from ..storage import sha256_file


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


class SatDumpProcessMixin:
    def build_command(self, manifest: SatDumpManifest, output_dir: Path) -> list[str]:
        arguments = [
            manifest.pipeline,
            manifest.input_level,
            str(manifest.input_file),
            str(output_dir),
        ]
        if manifest.samplerate is not None:
            arguments.extend(["--samplerate", str(manifest.samplerate)])
        if manifest.baseband_format:
            arguments.extend(["--baseband_format", manifest.baseband_format])
        arguments.extend(manifest.extra_args)
        return self._runner_command(arguments)

    @contextmanager
    def _output_lock(self, name: str) -> Iterator[None]:
        lock_dir = self.workspace.root / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"satdump-{name}.lock"
        with lock_path.open("a+") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"Обработка SatDump {name} уже выполняется") from exc
            stream.seek(0)
            stream.truncate()
            stream.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                )
            )
            stream.flush()
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _archive_existing(self, output_dir: Path, output_root: Path) -> None:
        if not output_dir.exists():
            return
        archive = output_root / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = archive / f"{output_dir.name}-{timestamp}"
        suffix = 1
        while target.exists():
            target = archive / f"{output_dir.name}-{timestamp}-{suffix}"
            suffix += 1
        shutil.move(str(output_dir), str(target))

    def process_manifest(self, manifest_path: str | Path, *, progress=None) -> dict[str, Any]:
        validation = self.validate(deep=True)
        if not validation["ok"]:
            raise RuntimeError("; ".join(validation["errors"]))
        manifest_path = Path(manifest_path).expanduser().resolve()
        manifest = self.manifest_class.load(manifest_path, self.cfg)
        if not manifest.input_file.is_file():
            raise FileNotFoundError(manifest.input_file)
        digest = sha256_file(manifest.input_file)
        output_name = manifest.output_name or f"{manifest.input_file.stem}-{digest[:10]}"
        safe_output_name = "".join(
            character if character.isalnum() or character in "-_." else "_"
            for character in output_name
        )
        output_root = resolve_path(
            self.cfg,
            self.satdump_cfg.get("output", "satdump-output"),
            workspace_relative=True,
        )
        output_root.mkdir(parents=True, exist_ok=True)
        output_dir = output_root / safe_output_name
        log_path = self.workspace.root / "logs" / "jobs" / f"satdump-{safe_output_name}.log"
        state_path = self.workspace.root / "monitoring" / "satdump" / f"{safe_output_name}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with self._output_lock(safe_output_name):
            provenance_path = output_dir / "satprof-provenance.json"
            if provenance_path.is_file() and bool(self.satdump_cfg.get("reuse_successful_output", True)):
                try:
                    previous = json.loads(provenance_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    previous = {}
                if previous.get("input_sha256") == digest and previous.get("status") == "success":
                    previous["reused"] = True
                    if progress:
                        progress(1.0, "Ранее обработанный результат использован повторно")
                    return previous

            partial_root = output_root / ".partial"
            partial_root.mkdir(parents=True, exist_ok=True)
            partial_dir = partial_root / f"{safe_output_name}-{uuid.uuid4().hex[:10]}"
            partial_dir.mkdir(parents=True, exist_ok=False)
            command = self.build_command(manifest, partial_dir)
            started = datetime.now(timezone.utc)
            run_state: dict[str, Any] = {
                "schema": "satprof.satdump-state/1",
                "status": "running",
                "output_name": safe_output_name,
                "manifest": str(manifest_path),
                "input_file": str(manifest.input_file),
                "input_sha256": digest,
                "command": command,
                "started_at": started.isoformat(),
                "pid": os.getpid(),
            }
            _atomic_json(state_path, run_state)
            if progress:
                progress(0.05, "Запуск SatDump")
            env = os.environ.copy()
            env["SATPROF_WORKSPACE"] = str(self.workspace.root)
            try:
                with log_path.open("ab") as log:
                    log.write(
                        (
                            "\n=== "
                            + started.isoformat()
                            + " ===\n"
                            + " ".join(command)
                            + "\n"
                        ).encode("utf-8")
                    )
                    process = subprocess.run(
                        command,
                        cwd=self.root,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=int(self.satdump_cfg.get("timeout_seconds", 7200)),
                        env=env,
                        check=False,
                    )
                if process.returncode != 0:
                    raise RuntimeError(
                        f"SatDump завершился с кодом {process.returncode}; журнал: {log_path}"
                    )
                completion = {
                    "input_sha256": digest,
                    "return_code": process.returncode,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
                _atomic_json(partial_dir / ".satdump-complete.json", completion)
                self._archive_existing(output_dir, output_root)
                os.replace(partial_dir, output_dir)
                if progress:
                    progress(0.72, "Импорт научных продуктов")
                ingested = self.ingest_outputs(manifest, output_dir)
                finished = datetime.now(timezone.utc)
                provenance = {
                    "schema": "satprof.satdump-run/2",
                    "status": "success",
                    "manifest": str(manifest_path),
                    "input_file": str(manifest.input_file),
                    "input_sha256": digest,
                    "pipeline": manifest.pipeline,
                    "input_level": manifest.input_level,
                    "instrument": manifest.instrument,
                    "satellite": manifest.satellite,
                    "command": command,
                    "satdump": validation,
                    "started_at": started.isoformat(),
                    "finished_at": finished.isoformat(),
                    "duration_seconds": (finished - started).total_seconds(),
                    "return_code": process.returncode,
                    "log": str(log_path),
                    "output_dir": str(output_dir),
                    "ingested": ingested,
                }
                _atomic_json(output_dir / "satprof-provenance.json", provenance)
                _atomic_json(state_path, provenance)
                done_dir = manifest_path.parent / "processed"
                done_dir.mkdir(parents=True, exist_ok=True)
                destination = done_dir / manifest_path.name
                if destination.exists():
                    destination = done_dir / (
                        manifest_path.stem
                        + "-"
                        + finished.strftime("%Y%m%dT%H%M%SZ")
                        + manifest_path.suffix
                    )
                shutil.move(str(manifest_path), str(destination))
                if progress:
                    progress(1.0, "SatDump и импорт завершены")
                return provenance
            except Exception as exc:
                failed_at = datetime.now(timezone.utc)
                failure = {
                    **run_state,
                    "status": "failed",
                    "failed_at": failed_at.isoformat(),
                    "duration_seconds": (failed_at - started).total_seconds(),
                    "error": str(exc),
                    "log": str(log_path),
                }
                _atomic_json(state_path, failure)
                failed_root = output_root / "failed"
                failed_root.mkdir(parents=True, exist_ok=True)
                if partial_dir.exists():
                    failed_target = failed_root / (
                        f"{safe_output_name}-{failed_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
                    )
                    shutil.move(str(partial_dir), str(failed_target))
                    _atomic_json(failed_target / "satprof-failure.json", failure)
                elif output_dir.exists() and not (output_dir / "satprof-provenance.json").exists():
                    _atomic_json(output_dir / "satprof-import-error.json", failure)
                raise

    def ingest_outputs(self, manifest: SatDumpManifest, output_dir: Path) -> list[dict[str, Any]]:
        reader_type = str(manifest.reader.get("type", "netcdf"))
        results: list[dict[str, Any]] = []
        metadata = {
            "satdump_output": str(output_dir),
            "satdump_branch": self.required_branch,
            "satdump_commit": self.detect_commit(),
            "satdump_install_prefix": str(self.install_prefix),
        }
        if reader_type == "netcdf":
            glob_pattern = manifest.reader.get("glob", "**/*.nc")
            mapping_value = manifest.reader.get("mapping")
            if not mapping_value:
                raise ValueError("Для reader.type=netcdf укажите reader.mapping")
            mapping_path = Path(mapping_value).expanduser()
            if not mapping_path.is_absolute():
                mapping_path = (
                    Path(self.cfg.get("_config_dir", Path.cwd())) / mapping_path
                ).resolve()
            files = sorted(output_dir.glob(glob_pattern))
            if not files:
                raise RuntimeError(
                    f"SatDump завершён, но по шаблону {glob_pattern} не найден Level 1C NetCDF"
                )
            for path in files:
                if self.workspace.input_was_processed("satdump_netcdf", path):
                    results.append(
                        {"file": str(path), "status": "skipped", "instrument": manifest.instrument}
                    )
                    continue
                granule = read_generic_netcdf(
                    path,
                    manifest.instrument,
                    mapping_path,
                    manifest.satellite,
                )
                granule.metadata.update(metadata)
                qc = check_satellite(granule)
                granule_id = self.workspace.store_granule(
                    granule,
                    {"passed": qc.passed, "flags": qc.flags, "metrics": qc.metrics},
                )
                self.workspace.record_input("satdump_netcdf", path, "ok", 1)
                results.append(
                    {
                        "file": str(path),
                        "status": "ok",
                        "instrument": manifest.instrument,
                        "granule_id": granule_id,
                        "qc_passed": qc.passed,
                    }
                )
        elif reader_type == "geotiff_manifest":
            glob_pattern = manifest.reader.get("glob", "**/*.satprof-level1c.json")
            files = sorted(output_dir.glob(glob_pattern))
            if not files:
                raise RuntimeError(f"Не найден манифест стека GeoTIFF: {glob_pattern}")
            for path in files:
                if self.workspace.input_was_processed("satdump_geotiff", path):
                    results.append(
                        {"file": str(path), "status": "skipped", "instrument": manifest.instrument}
                    )
                    continue
                granule = read_geotiff_stack_manifest(
                    path,
                    instrument=manifest.instrument,
                    satellite=manifest.satellite,
                )
                granule.metadata.update(metadata)
                qc = check_satellite(granule)
                granule_id = self.workspace.store_granule(
                    granule,
                    {"passed": qc.passed, "flags": qc.flags, "metrics": qc.metrics},
                )
                self.workspace.record_input("satdump_geotiff", path, "ok", 1)
                results.append(
                    {
                        "file": str(path),
                        "status": "ok",
                        "instrument": manifest.instrument,
                        "granule_id": granule_id,
                        "qc_passed": qc.passed,
                    }
                )
        else:
            raise ValueError(f"Неизвестный reader.type: {reader_type}")
        return results


