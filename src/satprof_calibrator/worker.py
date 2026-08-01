from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import argparse
import shutil
import signal
import time

from .config import load_config, workspace_path
from .integrations.satdump import SatDumpRunner, discover_manifests
from .jobs import Job, JobQueue
from .operations import sync_soundings, update_all_statistics, update_instrument
from .orbit import sync_tles
from .storage import Workspace


class SatProfWorker:
    def __init__(self, workspace: Workspace, cfg: dict[str, Any]):
        self.workspace = workspace
        self.cfg = cfg
        self.queue = JobQueue(workspace)
        self.stopping = False
        self.handlers = {
            "source.sync": self._source_sync,
            "tle.sync": self._tle_sync,
            "satdump.scan": self._satdump_scan,
            "satdump.process": self._satdump_process,
            "instrument.refresh": self._instrument_refresh,
            "calibration.train": self._calibration_train,
            "statistics.update": self._statistics_update,
        }

    def _progress(self, job: Job):
        return lambda value, message: self.queue.update(job.id, progress=value, message=message)

    def _source_sync(self, job, progress):
        result = sync_soundings(self.workspace, self.cfg, progress=progress)
        for instrument in self.cfg.get("instruments", {}):
            self.queue.enqueue(
                "instrument.refresh",
                {"instrument": instrument, "fit_bias": False},
                dedupe_key=f"instrument.refresh:{instrument}",
            )
        return result

    def _tle_sync(self, job, progress):
        progress(0.05, "Получение актуальных TLE")
        result = sync_tles(self.workspace, self.cfg)
        progress(1.0, "Каталог орбит обновлён")
        return result

    def _satdump_scan(self, job, progress):
        manifests = discover_manifests(self.workspace, self.cfg)
        for index, manifest in enumerate(manifests):
            self.queue.enqueue(
                "satdump.process",
                {"manifest": str(manifest)},
                priority=30,
                dedupe_key=f"satdump.process:{manifest.resolve()}",
                max_attempts=int(self.cfg.get("worker", {}).get("max_attempts", 3)),
            )
            progress(
                (index + 1) / max(len(manifests), 1),
                f"Найдено манифестов: {len(manifests)}",
            )
        return {"manifests": [str(path) for path in manifests], "enqueued": len(manifests)}

    @staticmethod
    def _archive_reused_manifest(manifest_path: Path) -> Path | None:
        if not manifest_path.is_file():
            return None
        processed = manifest_path.parent / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        destination = processed / manifest_path.name
        if destination.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            destination = processed / (
                manifest_path.stem + "-" + timestamp + manifest_path.suffix
            )
        shutil.move(str(manifest_path), str(destination))
        return destination

    def _satdump_process(self, job, progress):
        manifest_path = Path(job.payload["manifest"])
        result = SatDumpRunner(self.workspace, self.cfg).process_manifest(
            manifest_path,
            progress=progress,
        )
        if result.get("reused"):
            archived = self._archive_reused_manifest(manifest_path)
            if archived is not None:
                result["manifest_archived_to"] = str(archived)
        instruments = sorted(
            {
                item.get("instrument")
                for item in result.get("ingested", [])
                if item.get("instrument")
            }
        )
        if not instruments and result.get("instrument"):
            instruments = [str(result["instrument"])]
        for instrument in instruments:
            self.queue.enqueue(
                "instrument.refresh",
                {"instrument": instrument, "fit_bias": False},
                dedupe_key=f"instrument.refresh:{instrument}",
            )
        return result

    def _instrument_refresh(self, job, progress):
        return update_instrument(
            self.workspace,
            self.cfg,
            str(job.payload["instrument"]),
            fit_bias=bool(job.payload.get("fit_bias", False)),
            train_retrieval_model=bool(job.payload.get("train_retrieval", False)),
            progress=progress,
        )

    def _calibration_train(self, job, progress):
        instruments = (
            [job.payload["instrument"]]
            if job.payload.get("instrument")
            else list(self.cfg.get("instruments", {}))
        )
        output = {}
        for index, instrument in enumerate(instruments):
            start = index / max(len(instruments), 1)
            span = 1 / max(len(instruments), 1)
            output[instrument] = update_instrument(
                self.workspace,
                self.cfg,
                instrument,
                fit_bias=True,
                train_retrieval_model=True,
                progress=lambda value, message, start=start, span=span: progress(
                    start + span * value,
                    f"{instrument}: {message}",
                ),
            )
        return output

    def _statistics_update(self, job, progress):
        progress(0.1, "Пересчёт суточной статистики")
        result = update_all_statistics(self.workspace, self.cfg)
        progress(1.0, "Статистика обновлена")
        return result

    def schedule_due(self) -> list[int]:
        now = time.time()
        schedules = self.cfg.get("worker", {}).get("schedules", {})
        definitions = {
            "sync_soundings": ("source.sync", {}, "schedule:source.sync"),
            "scan_satdump": ("satdump.scan", {}, "schedule:satdump.scan"),
            "sync_tle": ("tle.sync", {}, "schedule:tle.sync"),
            "update_statistics": (
                "statistics.update",
                {},
                "schedule:statistics.update",
            ),
            "train_calibration": (
                "calibration.train",
                {},
                "schedule:calibration.train",
            ),
        }
        queued = []
        for name, (job_type, payload, dedupe) in definitions.items():
            interval = int(schedules.get(name, 0))
            if interval <= 0:
                continue
            state_key = f"schedule.last:{name}"
            previous = float(self.workspace.get_state(state_key, 0.0))
            if now - previous >= interval:
                queued.append(
                    self.queue.enqueue(job_type, payload, dedupe_key=dedupe)
                )
                self.workspace.set_state(state_key, now)
        return queued

    def run_once(self, *, schedule: bool = True) -> bool:
        if schedule:
            self.schedule_due()
        self.queue.recover_stale(
            int(self.cfg.get("worker", {}).get("stale_job_minutes", 60))
        )
        job = self.queue.claim()
        if job is None:
            return False
        handler = self.handlers.get(job.job_type)
        if handler is None:
            self.queue.fail(
                job,
                RuntimeError(f"Неизвестный тип задания: {job.job_type}"),
                retry_delay_seconds=0,
            )
            return True
        try:
            self.queue.complete(job.id, handler(job, self._progress(job)))
        except Exception as exc:
            self.workspace.emit_event(
                "job.failed",
                f"Задание {job.job_type} завершилось ошибкой",
                severity="error",
                details={"job_id": job.id, "error": str(exc)},
            )
            self.queue.fail(
                job,
                exc,
                retry_delay_seconds=int(
                    self.cfg.get("worker", {}).get("retry_delay_seconds", 120)
                ),
            )
        return True

    def run_forever(self):
        poll = max(1.0, float(self.cfg.get("worker", {}).get("poll_seconds", 5)))
        while not self.stopping:
            if not self.run_once(schedule=True):
                time.sleep(poll)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Фоновый рабочий процесс SatProf")
    parser.add_argument("--config")
    parser.add_argument("--workspace")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    workspace = Workspace(workspace_path(cfg, args.workspace))
    workspace.init()
    worker = SatProfWorker(workspace, cfg)
    signal.signal(signal.SIGTERM, lambda *_: setattr(worker, "stopping", True))
    signal.signal(signal.SIGINT, lambda *_: setattr(worker, "stopping", True))
    worker.run_once(schedule=True) if args.once else worker.run_forever()


if __name__ == "__main__":
    main()
