from pathlib import Path

from satprof_calibrator.integrations.satdump import SatDumpManifest, SatDumpRunner
from satprof_calibrator.storage import Workspace


def make_runtime(tmp_path: Path):
    root = tmp_path / "SatDump"
    runner = root / "scripts" / "astra" / "run.sh"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        """#!/usr/bin/env bash
set -e
prefix=''
while (($#)); do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    --) shift; break ;;
    *) shift ;;
  esac
done
[[ -x "$prefix/bin/satdump" ]]
printf 'SatDump test 1.2.2\\n'
""",
        encoding="utf-8",
    )
    prefix = tmp_path / "satdump" / "current"
    (prefix / "bin").mkdir(parents=True)
    binary = prefix / "bin" / "satdump"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    (prefix / "share" / "satdump" / "resources").mkdir(parents=True)
    (prefix / "share" / "satdump" / "pipelines").mkdir(parents=True)
    (prefix / "share" / "satdump" / "satdump_cfg.json").write_text("{}", encoding="utf-8")
    (prefix / ".satdump-install-root").write_text("version=1.2.2\nsource=abc123\n", encoding="utf-8")
    return root, prefix


def test_satdump_uses_explicit_install_prefix(tmp_path):
    root, prefix = make_runtime(tmp_path)
    workspace = Workspace(tmp_path / "workspace")
    workspace.init()
    cfg = {
        "_config_dir": str(tmp_path),
        "workspace": str(workspace.root),
        "satdump": {
            "root": str(root),
            "runner": "scripts/astra/run.sh",
            "install_prefix": str(prefix),
            "required_branch": "release/1.2.2",
        },
    }
    runner = SatDumpRunner(workspace, cfg)
    result = runner.validate(deep=True)
    assert result["ok"] is True
    assert result["probe"]["ok"] is True
    manifest = SatDumpManifest(
        input_file=tmp_path / "input.cs16",
        pipeline="meteor_m2x_lrpt",
        input_level="baseband",
        instrument="mtvza_gy",
        satellite="meteor",
        samplerate=240000,
        baseband_format="cs16",
        extra_args=[],
        reader={"type": "netcdf"},
    )
    command = runner.build_command(manifest, tmp_path / "output")
    assert command[:4] == ["bash", str(root / "scripts/astra/run.sh"), "--prefix", str(prefix)]
    assert command[4] == "--"
