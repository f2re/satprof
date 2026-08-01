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
from ..satdump_level1c import read_satdump_level1c_manifest
from ..satellite import read_generic_netcdf, read_geotiff_stack_manifest
from ..storage import sha256_file


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


class SatDumpProcessMixin:
    def build_command(self, manifest: SatDumpManifest, output_dir: Path) -> list[str]:
        arguments = [manifest.pipeline, manifest.input_level, str(manifest.input_file), str(output_dir)]
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
            stream.write(json.dumps({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False))
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
        safe_output_name = "".join(character if character.isalnum() or character in "-_." else "_" for character in output_name)
        output_root = resolve_path(self.cfg, self.satdump_cfg.get("output", "satdump-output"), workspace_relative=True)
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
                "schema": "satprof.satdump-state/1", "status": "running", "output_name": safe_output_name,
                "manifest": str(manifest_path), "input_file": str(manifest.input_file), "input_sha256": digest,
                "command": command, "started_at": started.isoformat(), "pid": os.getpid(),
            }
            _atomic_json(state_path, run_state)
            if progress:
                progress(0.05, "Запуск SatDump")
            environment = os.environ.copy()
            environment["SATPROF_WORKSPACE"] = str(self.workspace.root)
            try:
                with log_path.open("ab") as log:
                    log.write(("\n=== " + started.isoformat() + " ===\n" + " ".join(command) + "\n").encode("utf-8"))
                    process = subprocess.run(command, cwd=self.root, stdout=log, stderr=subprocess.STDOUT, timeout=int(self.satdump_cfg.get("timeout_seconds", 7200)), env=environment, check=False)
                if process.returncode != 0:
                    raise RuntimeError(f"SatDump завершился с кодом {process.returncode}; журнал: {log_path}")
                _atomic_json(partial_dir / ".satdump-complete.json", {"input_sha256": digest, "return_code": process.returncode, "completed_at": datetime.now(timezone.utc).isoformat()})
                self._archive_existing(output_dir, output_root)
                os.replace(partial_dir, output_dir)
                if progress:
                    progress(0.70, "Экспорт и импорт физического Level-1C")
                ingested = self.ingest_outputs(manifest, output_dir)
                finished = datetime.now(timezone.utc)
                provenance = {
                    "schema": "satprof.satdump-run/3", "status": "success", "manifest": str(manifest_path),
                    "input_file": str(manifest.input_file), "input_sha256": digest, "pipeline": manifest.pipeline,
                    "input_level": manifest.input_level, "instrument": manifest.instrument, "satellite": manifest.satellite,
                    "command": command, "satdump": validation, "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                    "duration_seconds": (finished - started).total_seconds(), "return_code": process.returncode,
                    "log": str(log_path), "output_dir": str(output_dir), "ingested": ingested,
                }
                _atomic_json(output_dir / "satprof-provenance.json", provenance)
                _atomic_json(state_path, provenance)
                done_dir = manifest_path.parent / "processed"
                done_dir.mkdir(parents=True, exist_ok=True)
                destination = done_dir / manifest_path.name
                if destination.exists():
                    destination = done_dir / (manifest_path.stem + "-" + finished.strftime("%Y%m%dT%H%M%SZ") + manifest_path.suffix)
                shutil.move(str(manifest_path), str(destination))
                if progress:
                    progress(1.0, "SatDump и импорт завершены")
                return provenance
            except Exception as exc:
                failed_at = datetime.now(timezone.utc)
                failure = {**run_state, "status": "failed", "failed_at": failed_at.isoformat(), "duration_seconds": (failed_at - started).total_seconds(), "error": str(exc), "log": str(log_path)}
                _atomic_json(state_path, failure)
                failed_root = output_root / "failed"
                failed_root.mkdir(parents=True, exist_ok=True)
                source = partial_dir if partial_dir.exists() else output_dir
                if source.exists() and not (source / "satprof-provenance.json").exists():
                    failed_target = failed_root / f"{safe_output_name}-{failed_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
                    shutil.move(str(source), str(failed_target))
                    _atomic_json(failed_target / "satprof-failure.json", failure)
                raise

    @staticmethod
    def _default_product_globs(instrument: str) -> list[str]:
        return {
            "mtvza_gy": ["**/MTVZA/product.cbor"],
            "ikfs2": ["**/IKFS-2/product.cbor", "**/IKFS2/product.cbor", "**/IKFS*/product.cbor"],
            "msugs": ["**/MSU-GS/product.cbor", "**/MSUGS/product.cbor", "**/MSU_GS/product.cbor"],
        }.get(instrument, ["**/product.cbor"])

    def _export_satdump_level1c(self, manifest: SatDumpManifest, output_dir: Path) -> list[Path]:
        reader = manifest.reader
        configured = reader.get("product_glob") or self._default_product_globs(manifest.instrument)
        patterns = [configured] if isinstance(configured, str) else list(configured)
        products: list[Path] = []
        for pattern in patterns:
            products.extend(output_dir.glob(str(pattern)))
        products = sorted({path.resolve() for path in products if path.is_file()})
        if not products:
            raise RuntimeError("SatDump не создал product.cbor для прибора; проверены шаблоны: " + ", ".join(patterns))
        manifests: list[Path] = []
        for product in products:
            export_dir = product.parent / "satprof-level1c"
            arguments = ["level1c", str(product.parent), str(export_dir), "--instrument", manifest.instrument, "--satellite", manifest.satellite, "--stride", str(max(1, int(reader.get("stride", 1)))), "--overwrite"]
            channels = reader.get("channels")
            if channels:
                if isinstance(channels, (list, tuple)):
                    channels = ",".join(str(item) for item in channels)
                arguments.extend(["--channels", str(channels)])
            if reader.get("require_calibrated", False):
                arguments.append("--require-calibrated")
            command = self._runner_command(arguments)
            process = subprocess.run(command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=int(reader.get("export_timeout_seconds", 3600)), check=False, env={**os.environ, "SATPROF_WORKSPACE": str(self.workspace.root)})
            if process.returncode != 0:
                raise RuntimeError(f"Экспорт SatDump Level-1C завершился с кодом {process.returncode}: {process.stdout[-8000:]}")
            manifests.extend(sorted(export_dir.glob("**/satprof-level1c.json")))
        if not manifests:
            raise RuntimeError("Команда SatDump level1c не создала манифесты")
        return manifests

    def _ingest_binary_manifests(self, manifest: SatDumpManifest, files: list[Path], metadata: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for path in files:
            if self.workspace.input_was_processed("satdump_level1c_binary", path):
                results.append({"file": str(path), "status": "skipped", "instrument": manifest.instrument})
                continue
            granule = read_satdump_level1c_manifest(path, instrument=manifest.instrument, satellite=manifest.satellite)
            granule.metadata.update(metadata)
            qc = check_satellite(granule)
            granule_id = self.workspace.store_granule(granule, {"passed": qc.passed, "flags": qc.flags, "metrics": qc.metrics})
            self.workspace.record_input("satdump_level1c_binary", path, "ok", 1)
            results.append({"file": str(path), "status": "ok", "instrument": manifest.instrument, "granule_id": granule_id, "qc_passed": qc.passed, "calibration_state": granule.metadata.get("calibration_state"), "raw_counts_available": granule.raw_counts is not None})
        return results

    def ingest_outputs(self, manifest: SatDumpManifest, output_dir: Path) -> list[dict[str, Any]]:
        reader_type = str(manifest.reader.get("type", "auto"))
        metadata = {"satdump_output": str(output_dir), "satdump_branch": self.required_branch, "satdump_commit": self.detect_commit(), "satdump_install_prefix": str(self.install_prefix)}
        if reader_type in {"auto", "satprof_binary"}:
            files = sorted(output_dir.glob("**/satprof-level1c.json"))
            if not files and reader_type == "auto":
                files = self._export_satdump_level1c(manifest, output_dir)
            if files:
                return self._ingest_binary_manifests(manifest, files, metadata)
            if reader_type == "satprof_binary":
                raise RuntimeError("Не найден satprof-level1c.json")

        results: list[dict[str, Any]] = []
        if reader_type in {"auto", "netcdf"} and manifest.reader.get("mapping"):
            glob_pattern = manifest.reader.get("glob", "**/*.nc")
            mapping_path = Path(manifest.reader["mapping"]).expanduser()
            if not mapping_path.is_absolute():
                mapping_path = (Path(self.cfg.get("_config_dir", Path.cwd())) / mapping_path).resolve()
            files = sorted(output_dir.glob(glob_pattern))
            if files:
                for path in files:
                    if self.workspace.input_was_processed("satdump_netcdf", path):
                        results.append({"file": str(path), "status": "skipped", "instrument": manifest.instrument})
                        continue
                    granule = read_generic_netcdf(path, manifest.instrument, mapping_path, manifest.satellite)
                    granule.metadata.update(metadata)
                    qc = check_satellite(granule)
                    granule_id = self.workspace.store_granule(granule, {"passed": qc.passed, "flags": qc.flags, "metrics": qc.metrics})
                    self.workspace.record_input("satdump_netcdf", path, "ok", 1)
                    results.append({"file": str(path), "status": "ok", "instrument": manifest.instrument, "granule_id": granule_id, "qc_passed": qc.passed})
                return results
            if reader_type == "netcdf":
                raise RuntimeError(f"SatDump завершён, но по шаблону {glob_pattern} не найден Level-1C NetCDF")

        if reader_type in {"auto", "geotiff_manifest"}:
            glob_pattern = manifest.reader.get("glob", "**/*.satprof-level1c.json")
            files = sorted(output_dir.glob(glob_pattern))
            for path in files:
                if self.workspace.input_was_processed("satdump_geotiff", path):
                    results.append({"file": str(path), "status": "skipped", "instrument": manifest.instrument})
                    continue
                granule = read_geotiff_stack_manifest(path, instrument=manifest.instrument, satellite=manifest.satellite)
                granule.metadata.update(metadata)
                qc = check_satellite(granule)
                granule_id = self.workspace.store_granule(granule, {"passed": qc.passed, "flags": qc.flags, "metrics": qc.metrics})
                self.workspace.record_input("satdump_geotiff", path, "ok", 1)
                results.append({"file": str(path), "status": "ok", "instrument": manifest.instrument, "granule_id": granule_id, "qc_passed": qc.passed})
            if results:
                return results
        raise ValueError(f"Не удалось получить Level-1C для reader.type={reader_type}")
