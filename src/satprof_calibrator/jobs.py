from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import json
import socket
import traceback

from .storage import Workspace, utcnow_iso


@dataclass
class Job:
    id: int
    job_type: str
    payload: dict[str, Any]
    status: str
    priority: int
    attempts: int
    max_attempts: int
    available_at: str
    progress: float
    message: str | None = None


class JobQueue:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.workspace.init()

    def enqueue(self, job_type: str, payload: dict[str, Any] | None = None, *, priority: int = 100, max_attempts: int = 3, dedupe_key: str | None = None, available_at: datetime | None = None) -> int:
        now = utcnow_iso(); available = (available_at or datetime.now(timezone.utc)).isoformat()
        with self.workspace.connect(immediate=True) as db:
            if dedupe_key:
                row = db.execute("SELECT id FROM jobs WHERE dedupe_key=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1", (dedupe_key,)).fetchone()
                if row: return int(row["id"])
            cursor = db.execute("""INSERT INTO jobs(job_type,payload_json,dedupe_key,status,priority,attempts,max_attempts,available_at,progress,created_at,updated_at)
                VALUES(?,?,?,'queued',?,0,?,?,0,?,?)""",(job_type,json.dumps(payload or {}, ensure_ascii=False),dedupe_key,int(priority),int(max_attempts),available,now,now))
            return int(cursor.lastrowid)

    def recover_stale(self, stale_minutes: int = 60) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()
        with self.workspace.connect(immediate=True) as db:
            rows = db.execute("SELECT id,attempts,max_attempts FROM jobs WHERE status='running' AND locked_at<?", (cutoff,)).fetchall(); recovered = 0
            for row in rows:
                status = "failed" if int(row["attempts"]) >= int(row["max_attempts"]) else "queued"
                db.execute("UPDATE jobs SET status=?,worker_id=NULL,locked_at=NULL,message=?,updated_at=? WHERE id=?", (status,"Восстановлено после зависшего рабочего процесса",utcnow_iso(),int(row["id"])))
                recovered += 1
            return recovered

    def claim(self, worker_id: str | None = None) -> Job | None:
        worker_id = worker_id or f"{socket.gethostname()}:{os_getpid()}"; now = utcnow_iso()
        with self.workspace.connect(immediate=True) as db:
            row = db.execute("""SELECT * FROM jobs WHERE status='queued' AND available_at<=? ORDER BY priority ASC,id ASC LIMIT 1""", (now,)).fetchone()
            if row is None: return None
            attempts = int(row["attempts"]) + 1
            db.execute("""UPDATE jobs SET status='running',attempts=?,locked_at=?,worker_id=?,progress=0,message='Запущено',updated_at=? WHERE id=? AND status='queued'""", (attempts,now,worker_id,now,int(row["id"])))
            return Job(id=int(row["id"]),job_type=str(row["job_type"]),payload=json.loads(row["payload_json"]),status="running",priority=int(row["priority"]),attempts=attempts,max_attempts=int(row["max_attempts"]),available_at=str(row["available_at"]),progress=0.0,message="Запущено")

    def update(self, job_id: int, *, progress: float | None = None, message: str | None = None) -> None:
        fields: list[str] = ["updated_at=?"]; values: list[Any] = [utcnow_iso()]
        if progress is not None: fields.append("progress=?"); values.append(float(max(0.0, min(1.0, progress))))
        if message is not None: fields.append("message=?"); values.append(message)
        values.append(int(job_id))
        with self.workspace.connect() as db: db.execute(f"UPDATE jobs SET {','.join(fields)} WHERE id=?", values)

    def complete(self, job_id: int, result: Any = None, *, message: str = "Завершено") -> None:
        with self.workspace.connect() as db:
            db.execute("""UPDATE jobs SET status='done',progress=1,message=?,result_json=?,error=NULL,locked_at=NULL,worker_id=NULL,updated_at=? WHERE id=?""", (message,json.dumps(result, ensure_ascii=False, default=str),utcnow_iso(),int(job_id)))

    def fail(self, job: Job, error: BaseException, *, retry_delay_seconds: int = 60) -> None:
        stack = "".join(traceback.format_exception(type(error), error, error.__traceback__)); retry = job.attempts < job.max_attempts; status = "queued" if retry else "failed"
        available = (datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)).isoformat(); message = f"Ошибка; повтор {job.attempts}/{job.max_attempts}" if retry else "Ошибка; попытки исчерпаны"
        with self.workspace.connect() as db:
            db.execute("""UPDATE jobs SET status=?,available_at=?,message=?,error=?,locked_at=NULL,worker_id=NULL,updated_at=? WHERE id=?""", (status,available,message,stack[-20000:],utcnow_iso(),int(job.id)))

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs"; params: list[Any] = []
        if status: sql += " WHERE status=?"; params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(int(limit))
        with self.workspace.connect() as db: rows = db.execute(sql, params).fetchall()
        output = []
        for row in rows:
            item = dict(row); item["payload"] = json.loads(item.pop("payload_json")); item["result"] = json.loads(item["result_json"]) if item.get("result_json") else None; item.pop("result_json", None); output.append(item)
        return output

    def get(self, job_id: int) -> dict[str, Any] | None:
        with self.workspace.connect() as db: row = db.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if row is None: return None
        item = dict(row); item["payload"] = json.loads(item.pop("payload_json")); item["result"] = json.loads(item["result_json"]) if item.get("result_json") else None; item.pop("result_json", None); return item


def os_getpid() -> int:
    import os
    return os.getpid()
