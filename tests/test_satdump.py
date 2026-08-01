import json
from pathlib import Path
from satprof_calibrator.integrations.satdump import SatDumpManifest, SatDumpRunner
from satprof_calibrator.storage import Workspace


def test_satdump_branch_and_command(tmp_path):
    root = tmp_path / "SatDump"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/release/1.2.2\n")
    runner = root / "scripts" / "astra" / "run.sh"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/bin/sh\n")
    input_file = tmp_path / "pass.cs16"; input_file.write_bytes(b"abc")
    manifest_path = tmp_path / "job.satprof.json"
    manifest_path.write_text(json.dumps({
        "input_file": str(input_file), "pipeline": "meteor_m2x_lrpt", "input_level": "baseband",
        "instrument": "mtvza_gy", "satellite": "METEOR", "samplerate": 240000,
        "baseband_format": "cs16", "reader": {"type": "netcdf", "mapping": "map.yaml"}
    }))
    cfg = {"_config_dir": str(tmp_path), "workspace": str(tmp_path / "ws"), "satdump": {"root": str(root), "runner": "scripts/astra/run.sh", "required_branch": "release/1.2.2"}}
    ws = Workspace(tmp_path / "ws"); ws.init()
    satdump = SatDumpRunner(ws, cfg)
    assert satdump.validate()["ok"] is True
    manifest = SatDumpManifest.load(manifest_path, cfg)
    command = satdump.build_command(manifest, tmp_path / "out")
    assert command[:3] == ["bash", str(runner), "--"]
    assert "meteor_m2x_lrpt" in command and "--samplerate" in command
