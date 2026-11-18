"""One background worker, one job at a time, a queue for the rest, state saved after every step."""
import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config
import settings

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "logs" / "jobs.json"
F1TV_OFF_TEXT = "F1TV downloads are off"
RESUMABLE = ("archive_f1tv", "classic")
HISTORY_KEEP = 30


class JobPaused(Exception):
    """Raised by a handler to stop the job in a resumable state (for example sign-in needed)."""

    def __init__(self, state_text):
        super().__init__(state_text)
        self.state_text = state_text


class JobCancelled(Exception):
    pass


@contextmanager
def atomic_dir(final):
    """Build into a temp folder next to final and rename at the end. A failure removes the temp folder."""
    final = Path(final)
    tmp = final.with_name(final.name + "_tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        yield tmp
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Context:
    def __init__(self, runner, job):
        self.runner, self.job = runner, job

    def progress(self, text, done=None, total=None, **extra):
        with self.runner.lock:
            self.job["text"] = text
            if done is not None:
                self.job["done"] = done
            if total is not None:
                self.job["total"] = total
            if extra:
                self.job.setdefault("extra", {}).update(extra)
            self.runner.save()

    def cancelled(self):
        return self.runner.stop_flag.is_set()

    def sleep(self, seconds):
        """Sleep in short slices. Returns False if the job was cancelled or paused meanwhile."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.cancelled():
                return False
            self.runner.sleep(min(config.JOB_POLL_S, max(0.0, end - time.monotonic())))
        return True


class JobRunner:
    def __init__(self, handlers, state_path=None, start=True, sleep=time.sleep):
        """handlers: {kind: callable(ctx, params) or (callable, requires_f1tv)}."""
        self.handlers = {}
        for kind, h in handlers.items():
            self.handlers[kind] = h if isinstance(h, tuple) else (h, False)
        self.state_path = Path(state_path or STATE_PATH)
        self.sleep = sleep
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop_flag = threading.Event()
        self.stop_reason = None
        self.shutdown = False
        self.jobs = []
        self.counter = 0
        self.thread = None
        self.load()
        if start:
            self.thread = threading.Thread(target=self.loop, daemon=True, name="job-runner")
            self.thread.start()

    # ---- state
    def load(self):
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            data = {}
        self.counter = data.get("counter", 0)
        self.jobs = data.get("jobs", [])
        for j in self.jobs:
            if j["state"] in ("running", "queued"):
                if j["kind"] in RESUMABLE:
                    j["state"], j["text"] = "paused", "paused when the app closed"
                else:
                    j["state"], j["error"], j["text"] = "failed", "interrupted when the app closed", "interrupted"

    def save(self):
        with self.lock:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            keep = self.jobs[-HISTORY_KEEP:]
            ids = {j["id"] for j in keep}
            keep = [j for j in self.jobs if j["id"] in ids or j["state"] in ("queued", "running", "paused")]
            data = {"counter": self.counter, "jobs": keep}
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, default=str))
            os.replace(tmp, self.state_path)

    # ---- public
    def submit(self, kind, params=None, label=None):
        if kind not in self.handlers:
            raise ValueError(f"unknown job kind {kind}")
        with self.lock:
            self.counter += 1
            job = {"id": self.counter, "kind": kind, "params": params or {}, "label": label or kind, "state": "queued",
                   "text": "waiting in the queue", "done": 0, "total": 0, "extra": {}, "error": None,
                   "created": now_iso(), "started": None, "finished": None}
            self.jobs.append(job)
            self.save()
        self.wake.set()
        return job

    def running(self):
        with self.lock:
            return next((j for j in self.jobs if j["state"] == "running"), None)

    def busy(self):
        with self.lock:
            return any(j["state"] in ("running", "queued") for j in self.jobs)

    def active(self, kind=None):
        with self.lock:
            return [j for j in self.jobs if j["state"] in ("running", "queued") and (kind is None or j["kind"] == kind)]

    def cancel(self):
        """Cancel the running job and drop queued jobs."""
        with self.lock:
            for j in self.jobs:
                if j["state"] == "queued":
                    j["state"], j["text"], j["finished"] = "cancelled", "cancelled", now_iso()
            if self.running():
                self.stop_reason = "cancel"
                self.stop_flag.set()
            self.save()

    def pause(self):
        with self.lock:
            if self.running():
                self.stop_reason = "pause"
                self.stop_flag.set()
            for j in self.jobs:
                if j["state"] == "queued" and j["kind"] in RESUMABLE:
                    j["state"], j["text"] = "paused", "paused"
            self.save()

    def resume(self):
        n = 0
        with self.lock:
            for j in self.jobs:
                if j["state"] == "paused":
                    j["state"], j["text"], j["error"] = "queued", "waiting in the queue", None
                    n += 1
            self.save()
        self.wake.set()
        return n

    def status(self):
        with self.lock:
            run = self.running()
            return {"running": dict(run) if run else None,
                    "queue": [dict(j) for j in self.jobs if j["state"] == "queued"],
                    "paused": [dict(j) for j in self.jobs if j["state"] == "paused"],
                    "recent": [dict(j) for j in self.jobs if j["state"] in ("done", "failed", "cancelled")][-5:]}

    def stop(self):
        self.shutdown = True
        self.stop_reason = "pause"
        self.stop_flag.set()
        self.wake.set()

    def wait_idle(self, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.busy():
                return True
            time.sleep(0.01)
        return False

    # ---- worker
    def next_job(self):
        with self.lock:
            return next((j for j in self.jobs if j["state"] == "queued"), None)

    def loop(self):
        while not self.shutdown:
            job = self.next_job()
            if job is None:
                self.wake.wait(0.5)
                self.wake.clear()
                continue
            self.execute(job)

    def execute(self, job):
        handler, needs_f1tv = self.handlers[job["kind"]]
        with self.lock:
            job["state"], job["started"], job["error"] = "running", now_iso(), None
            self.stop_flag.clear()
            self.stop_reason = None
            self.save()
        if needs_f1tv and not settings.f1tv_enabled():
            self.finish(job, "failed", F1TV_OFF_TEXT, F1TV_OFF_TEXT)
            return
        ctx = Context(self, job)
        try:
            result = handler(ctx, job["params"])
            if self.stop_flag.is_set() and self.stop_reason in ("pause", "cancel") and result is None:
                state = "paused" if self.stop_reason == "pause" and job["kind"] in RESUMABLE else "cancelled"
                self.finish(job, state, state)
            else:
                job["result"] = result
                self.finish(job, "done", job.get("text") or "done")
        except JobPaused as exc:
            self.finish(job, "paused", exc.state_text, extra={"pause_state": exc.state_text})
        except JobCancelled:
            self.finish(job, "cancelled", "cancelled")
        except Exception as exc:
            self.finish(job, "failed", "failed", f"{type(exc).__name__}: {exc}")

    def finish(self, job, state, text, error=None, extra=None):
        with self.lock:
            job["state"], job["text"], job["error"], job["finished"] = state, text, error, now_iso()
            if extra:
                job.setdefault("extra", {}).update(extra)
            self.save()
