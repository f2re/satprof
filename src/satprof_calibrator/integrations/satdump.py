from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
import fcntl
import json
import os
import re
import shutil
import subprocess
import uuid

from .satdump_process import SatDumpProcessMixin
from ..config import resolve_path
from ..qc import check_satellite
from ..satellite import read_generic_netcdf, read_geotiff_stack_manifest
from ..storage import Workspace, sha256_file

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/+-]+$")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def _read_key_value(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            result[key.strip()] = value.strip()
    return result


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
        if data.get("schema") not in (None, "satprof.satdump-job/1"):
            raise ValueError(f"Неподдерживаемая схема манифеста: {data.get('schema')}")
        input_value = data.get("input_file") or data.get("input")
        if not input_value:
            raise ValueError("В манифесте SatDump отсутствует input_file")
        input_file = Path(input_value).expanduser()
        if not input_file.is_absolute():
            input_file = (manifest_path.parent / input_file).resolve()
        pipeline = str(data["pipeline"])
        input_level = str(data.get("input_level", "baseband"))
        for label, value in (("pipeline", pipeline), ("input_level", input_level)):
            if not _SAFE_TOKEN.fullmatch(value):
                raise ValueError(f"Недопустимое значение {label}: {value!r}")
        reader = dict(data.get("reader") or {})
        reader.setdefault("type", "netcdf")
        extra_args = [str(value) for value in data.get("extra_args", [])]
        if len(extra_args) > int(cfg.get("satdump", {}).get("max_extra_args", 64)):
            raise ValueError("Слишком много дополнительных аргументов SatDump")
        return cls(
            input_file=input_file,
            pipeline=pipeline,
            input_level=input_level,
            instrument=str(data["instrument"]),
            satellite=str(data.get("satellite", "unknown")),
            samplerate=int(data["samplerate"]) if data.get("samplerate") is not None else None,
            baseband_format=str(data["baseband_format"]) if data.get("baseband_format") else None,
            extra_args=extra_args,
            reader=reader,
            output_name=data.get("output_name"),
        )


class SatDumpRunner(SatDumpProcessMixin):
    manifest_class = SatDumpManifest
    def __init__(self, workspace: Workspace, cfg: dict[str, Any]):
        self.workspace = workspace
        self.cfg = cfg
        self.satdump_cfg = cfg.get("satdump", {})
        self.root = resolve_path(cfg, self.satdump_cfg.get("root", "/opt/SatDump"))
        self.runner = self.root / self.satdump_cfg.get("runner", "scripts/astra/run.sh")
        self.required_branch = str(self.satdump_cfg.get("required_branch", "release/1.2.2"))
        self.install_prefix = resolve_path(
            cfg,
            self.satdump_cfg.get("install_prefix", "/opt/satdump/current"),
        )
        expected = self.satdump_cfg.get("expected_commit")
        self.expected_commit = str(expected).strip() if expected else None

    def _git(self, *args: str, timeout: int = 10) -> str | None:
        if not (self.root / ".git").exists():
            return None
        try:
            process = subprocess.run(
                ["git", "-C", str(self.root), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return process.stdout.strip() if process.returncode == 0 else None

    def detect_branch(self) -> str | None:
        branch = self._git("symbolic-ref", "--short", "HEAD")
        if branch:
            return branch
        head = self.root / ".git" / "HEAD"
        if not head.exists():
            return None
        text = head.read_text(encoding="utf-8", errors="replace").strip()
        prefix = "ref: refs/heads/"
        return text[len(prefix) :] if text.startswith(prefix) else text[:12]

    def detect_commit(self) -> str | None:
        commit = self._git("rev-parse", "HEAD")
        if commit:
            return commit
        marker = _read_key_value(self.install_prefix / ".satdump-install-root")
        return marker.get("source")

    def source_dirty(self) -> bool | None:
        if not (self.root / ".git").exists():
            return None
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "status", "--porcelain"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return bool(result.stdout.strip()) if result.returncode == 0 else None

    def _runtime_paths(self) -> dict[str, Path]:
        return {
            "binary": self.install_prefix / "bin" / "satdump",
            "resources": self.install_prefix / "share" / "satdump" / "resources",
            "pipelines": self.install_prefix / "share" / "satdump" / "pipelines",
            "config": self.install_prefix / "share" / "satdump" / "satdump_cfg.json",
            "marker": self.install_prefix / ".satdump-install-root",
        }

    def _runner_command(self, arguments: list[str]) -> list[str]:
        return [
            "bash",
            str(self.runner),
            "--prefix",
            str(self.install_prefix),
            "--",
            *arguments,
        ]

    def probe_runtime(self) -> dict[str, Any]:
        timeout = int(self.satdump_cfg.get("health_timeout_seconds", 30))
        command = self._runner_command(["version"])
        started = datetime.now(timezone.utc)
        try:
            process = subprocess.run(
                command,
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                check=False,
                env={**os.environ, "SATPROF_WORKSPACE": str(self.workspace.root)},
            )
            output = process.stdout[-8000:].strip()
            return {
                "ok": process.returncode == 0,
                "return_code": process.returncode,
                "command": command,
                "output": output,
                "duration_seconds": (
                    datetime.now(timezone.utc) - started
                ).total_seconds(),
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "return_code": None,
                "command": command,
                "output": (exc.stdout or "")[-8000:] if isinstance(exc.stdout, str) else "",
                "error": f"тайм-аут {timeout} с",
                "duration_seconds": (
                    datetime.now(timezone.utc) - started
                ).total_seconds(),
            }
        except OSError as exc:
            return {"ok": False, "return_code": None, "command": command, "error": str(exc)}

    def validate(self, *, deep: bool = False) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        paths = self._runtime_paths()
        if not self.root.is_dir():
            errors.append(f"Каталог исходников SatDump не найден: {self.root}")
        if not self.runner.is_file():
            errors.append(f"Сценарий запуска SatDump не найден: {self.runner}")
        branch = self.detect_branch()
        commit = self.detect_commit()
        dirty = self.source_dirty()
        if branch and branch != self.required_branch:
            if self.expected_commit and commit and commit.startswith(self.expected_commit):
                warnings.append(
                    f"Исходники находятся в detached HEAD {commit[:12]}, закреплённом expected_commit"
                )
            else:
                errors.append(f"Ожидалась ветка SatDump {self.required_branch}, найдена {branch}")
        if branch is None:
            warnings.append("Копия SatDump не содержит доступного Git-репозитория")
        if dirty:
            warnings.append("В исходниках SatDump имеются незакоммиченные изменения")
        if self.expected_commit and (not commit or not commit.startswith(self.expected_commit)):
            errors.append(
                f"Ожидался commit SatDump {self.expected_commit}, найден {commit or 'не определён'}"
            )
        if not self.install_prefix.is_dir():
            errors.append(f"Установочный prefix SatDump не найден: {self.install_prefix}")
        for key in ("binary", "resources", "pipelines", "config"):
            path = paths[key]
            if key == "binary":
                if not path.is_file() or not os.access(path, os.X_OK):
                    errors.append(f"Исполняемый файл SatDump не найден: {path}")
            elif key == "config":
                if not path.is_file():
                    errors.append(f"Конфигурация SatDump не найдена: {path}")
            elif not path.is_dir():
                errors.append(f"Каталог SatDump {key} не найден: {path}")
        marker = _read_key_value(paths["marker"])
        if not marker:
            warnings.append("Нет маркера .satdump-install-root; сборка не аттестована сценарием Astra")
        if marker.get("source") and commit and not commit.startswith(marker["source"]):
            warnings.append(
                "Установленный SatDump собран из другого commit, чем текущие исходники"
            )
        result: dict[str, Any] = {
            "ok": not errors,
            "root": str(self.root),
            "runner": str(self.runner),
            "install_prefix": str(self.install_prefix),
            "binary": str(paths["binary"]),
            "branch": branch,
            "required_branch": self.required_branch,
            "commit": commit,
            "expected_commit": self.expected_commit,
            "source_dirty": dirty,
            "marker": marker,
            "repository": self.satdump_cfg.get("repository"),
            "errors": errors,
            "warnings": warnings,
        }
        if deep and not errors:
            probe = self.probe_runtime()
            result["probe"] = probe
            if not probe.get("ok"):
                errors.append(
                    "SatDump не проходит запуск version: "
                    + str(probe.get("error") or probe.get("output") or "неизвестная ошибка")
                )
                result["ok"] = False
        return result


def discover_manifests(workspace: Workspace, cfg: dict[str, Any]) -> list[Path]:
    satdump_cfg = cfg.get("satdump", {})
    inbox = resolve_path(
        cfg,
        satdump_cfg.get("inbox", "inbox/satdump"),
        workspace_relative=True,
    )
    inbox.mkdir(parents=True, exist_ok=True)
    return sorted(
        path
        for path in inbox.glob(satdump_cfg.get("manifest_glob", "*.satprof.json"))
        if path.is_file()
    )
