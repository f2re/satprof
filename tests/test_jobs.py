from satprof_calibrator.jobs import JobQueue
from satprof_calibrator.storage import Workspace


def test_job_queue_deduplicates_and_completes(tmp_path):
    ws = Workspace(tmp_path / "workspace")
    queue = JobQueue(ws)
    first = queue.enqueue("tle.sync", {}, dedupe_key="tle")
    second = queue.enqueue("tle.sync", {}, dedupe_key="tle")
    assert first == second
    job = queue.claim("test-worker")
    assert job is not None and job.id == first
    queue.update(job.id, progress=0.5, message="половина")
    queue.complete(job.id, {"updated": 4})
    stored = queue.get(job.id)
    assert stored["status"] == "done"
    assert stored["result"]["updated"] == 4
