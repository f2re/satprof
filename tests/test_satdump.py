import json
import os
import time

from satprof_calibrator.integrations.satdump import (
    SatDumpManifest,
    SatDumpRunner,
    discover_manifests,
)
from satprof_calibrator.storage import Workspace


def _fake_runtime(tmp_path):
    root = tmp_path / "SatDump"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/release/1.2.2\n")
    runner = root / "scripts" / "astra" / "run.sh"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/bin/sh\n")
    prefix = tmp_path / "satdump-current"
    (prefix / "bin").mkdir(parents=True)
    binary = prefix / "bin" / "satdump"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (prefix / "share" / "satdump" / "resources").mkdir(parents=True)
    (prefix / "share" / "satdump" / "pipelines").mkdir(parents=True)
    (prefix / "share" / "satdump" / "satdump_cfg.json").write_text("{}")
    (prefix / ".satdump-install-root").write_text("source=test\n")
    return root, runner, prefix


def test_satdump_branch_and_command(tmp_path):
    root, runner, prefix = _fake_runtime(tmp_path)
    input_file = tmp_path / "pass.cs16"
    input_file.write_bytes(b"abc")
    manifest_path = tmp_path / "job.satprof.json"
    manifest_path.write_text(
        json.dumps(
            {
                "input_file": str(input_file),
                "pipeline": "meteor_m2x_lrpt",
                "input_level": "baseband",
                "instrument": "mtvza_gy",
                "satellite": "METEOR",
                "samplerate": 240000,
                "baseband_format": "cs16",
            }
        )
    )
    cfg = {
        "_config_dir": str(tmp_path),
        "workspace": str(tmp_path / "ws"),
        "satdump": {
            "root": str(root),
            "runner": "scripts/astra/run.sh",
            "install_prefix": str(prefix),
            "required_branch": "release/1.2.2",
        },
    }
    workspace = Workspace(tmp_path / "ws")
    workspace.init()
    satdump = SatDumpRunner(workspace, cfg)
    assert satdump.validate()["ok"] is True
    manifest = SatDumpManifest.load(manifest_path, cfg)
    assert manifest.reader["type"] == "auto"
    command = satdump.build_command(manifest, tmp_path / "out")
    assert command[:5] == ["bash", str(runner), "--prefix", str(prefix), "--"]
    assert "meteor_m2x_lrpt" in command and "--samplerate" in command


def test_watch_profile_creates_manifest_once(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.init()
    raw_dir = workspace.root / "inbox" / "raw"
    raw_dir.mkdir(parents=True)
    source = raw_dir / "pass.cadu"
    source.write_bytes(b"test-data")
    old = time.time() - 120
    os.utime(source, (old, old))
    cfg = {
        "_config_dir": str(tmp_path),
        "workspace": str(workspace.root),
        "satdump": {
            "inbox": "inbox/satdump",
            "watch_profiles": [
                {
                    "name": "meteor-xband",
                    "root": "inbox/raw",
                    "glob": "*.cadu",
                    "pipeline": "meteor_m2x_xband",
                    "input_level": "cadu",
                    "instrument": "mtvza_gy",
                    "satellite": "METEOR-M2-4",
                    "min_age_seconds": 30,
                    "reader": {"type": "auto"},
                }
            ],
        },
    }
    first = discover_manifests(workspace, cfg)
    second = discover_manifests(workspace, cfg)
    assert len(first) == 1
    assert first == second
    payload = json.loads(first[0].read_text())
    assert payload["reader"]["type"] == "auto"
    assert payload["watch"]["source_sha256"]
