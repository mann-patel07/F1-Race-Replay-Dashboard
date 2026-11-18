"""The F1TV archive job and the lap-only classic job. Never imports fastf1: sessions are archived by child processes."""
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bundle_io
import config
import f1_auth
import jobs
import settings

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / config.ARCHIVE_DIR
INDEX = ARCHIVE / "index.json"
SESSIONS = ARCHIVE / "sessions.json"
CACHE = ROOT / config.FASTF1_CACHE_DIR

OK, NO_DATA, FAILED = "ok", "no_data", "failed"
STATE_FINISHED = "finished, F1TV no longer needed"


def key_of(year, rnd, name):
    return f"{int(year)}/{int(rnd):02d}/{config.SESSION_CODES[name]}"


def read_index():
    try:
        return json.loads(INDEX.read_text())
    except (OSError, ValueError):
        return {"sessions": {}}


def write_index(data):
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=0))
    tmp.replace(INDEX)


def record(year, rnd, name, **fields):
    data = read_index()
    data["sessions"][key_of(year, rnd, name)] = {"year": year, "round": rnd, "session": name, **fields}
    write_index(data)


def folder_size(path):
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file()) if Path(path).exists() else 0


def free_gb(path=None):
    return shutil.disk_usage(str(path or ROOT)).free / 1e9


def order_sessions(sessions, phases=None):
    """Phase by phase in the order of ARCHIVE_PHASES; inside a phase newest season first, latest round first."""
    phases = phases or config.ARCHIVE_PHASES
    out = []
    for names in phases:
        part = [s for s in sessions if s["session"] in names]
        part.sort(key=lambda s: (-int(s["year"]), -int(s["round"])))
        out.append(part)
    return out


def list_sessions():
    """Ask a child process for the FastF1 schedule and read the saved list."""
    r = f1_auth.run_clean([f1_auth.child_python(), str(ROOT / "archive_one.py"), "--list-sessions"], config.FASTF1_SESSION_TIMEOUT_S)
    if r.returncode != 0 or not SESSIONS.exists():
        raise RuntimeError("could not read the FastF1 schedule: " + (r.stderr.strip().splitlines() or ["no output"])[-1])
    return json.loads(SESSIONS.read_text())


def run_one_process(year, rnd, name):
    """Run archive_one.py as its own process. Cleaned environment, closed standard input, session timeout."""
    cmd = [f1_auth.child_python(), str(ROOT / "archive_one.py"), "--year", str(year), "--round", str(rnd), "--session", name]
    try:
        r = f1_auth.run_clean(cmd, config.FASTF1_SESSION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 5, "timed out"
    lines = (r.stderr or r.stdout).strip().splitlines()
    return r.returncode, lines[-1] if lines else ""


def build_replay(year, rnd, name):
    import build_bundle_fastf1
    return build_bundle_fastf1.build(year, rnd, name)


def cleanup_cache():
    if not CACHE.exists():
        return
    for p in CACHE.iterdir():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


class Stats:
    def __init__(self):
        self.seconds = []
        self.sizes = []

    def add(self, seconds, size):
        self.seconds.append(seconds)
        self.sizes.append(size)

    def average(self):
        return sum(self.seconds) / len(self.seconds) if self.seconds else None


def run_archive(ctx, params=None):
    """The races_sprints part: archive every wanted FastF1 session, build bundles, switch F1TV off when complete."""
    if not settings.f1tv_enabled():
        raise RuntimeError(jobs.F1TV_OFF_TEXT)
    if free_gb() < config.ARCHIVE_MIN_FREE_GB:
        raise RuntimeError(f"free disk space is {free_gb():.1f} GB, below the {config.ARCHIVE_MIN_FREE_GB} GB needed to start")
    ctx.progress("reading the FastF1 schedule")
    sessions = list_sessions()
    now = datetime.now(timezone.utc)
    wanted = []
    for s in sessions:
        when = datetime.fromisoformat(s["date"])
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        if now - when > timedelta(hours=24):
            wanted.append(s)
    phases = order_sessions(wanted)
    total = sum(len(p) for p in phases)
    done_before = sum(1 for s in wanted if read_index()["sessions"].get(key_of(s["year"], s["round"], s["session"]), {}).get("status") in (OK, NO_DATA))
    stats = Stats()
    count = done_before
    extra = {"phases": [{"names": names, "total": len(p)} for names, p in zip(config.ARCHIVE_PHASES, phases)]}
    for pi, part in enumerate(phases):
        processed = 0
        for s in part:
            year, rnd, name = int(s["year"]), int(s["round"]), s["session"]
            status = read_index()["sessions"].get(key_of(year, rnd, name), {}).get("status")
            if status == OK:
                continue
            if ctx.cancelled():
                return None
            if free_gb() < config.ARCHIVE_STOP_FREE_GB:
                raise jobs.JobPaused("disk almost full")
            label = f"{year} round {rnd:02d} {s.get('event', '')} {name}".strip()
            started = time.time()
            waits, tried_again, outcome = 0, False, None
            while True:
                eta = None
                if len(stats.seconds) >= config.AUTO_ARCHIVE_CONFIRM_SESSIONS:
                    eta = stats.average() * (total - count)
                ctx.progress(label, count, total, current=label, state="running", average_s=stats.average(),
                             eta_s=eta, phase=pi, free_gb=round(free_gb(), 1), **extra)
                code, message = run_one_process(year, rnd, name)
                if code == 0:
                    outcome = OK
                elif code == 2:
                    waits += 1
                    if waits > config.FASTF1_RATE_MAX_WAITS:
                        outcome, message = FAILED, "gave up waiting for the FastF1 hourly limit"
                    else:
                        ctx.progress("waiting for the FastF1 hourly limit", count, total, current=label,
                                     state="waiting for the hourly limit")
                        if not ctx.sleep(config.FASTF1_RATE_WAIT_S):
                            return None
                        continue
                elif code == 3:
                    raise jobs.JobPaused("sign-in needed")
                elif code == 4:
                    outcome = NO_DATA
                else:
                    if not tried_again:
                        tried_again = True
                        if not ctx.sleep(config.RETRY_AFTER_FAILURE_S):
                            return None
                        continue
                    outcome = FAILED
                break
            seconds = round(time.time() - started, 1)
            size = folder_size(ARCHIVE / str(year) / f"{rnd:02d}" / config.SESSION_CODES[name])
            info = {}
            info_path = ARCHIVE / str(year) / f"{rnd:02d}" / config.SESSION_CODES[name] / "info.json"
            if outcome == OK and info_path.exists():
                info = json.loads(info_path.read_text())
            fields = {"status": outcome, "seconds": seconds, "size": size, "rows": info.get("rows", {}),
                      "event": s.get("event"), "finished": datetime.now(timezone.utc).isoformat()}
            if outcome == FAILED:
                fields["message"] = message
            record(year, rnd, name, **fields)
            if outcome == OK and name in config.REPLAY_SESSION_NAMES:
                try:
                    build_replay(year, rnd, name)
                except Exception as exc:
                    record(year, rnd, name, **{**fields, "bundle_error": f"{type(exc).__name__}: {exc}"})
            cleanup_cache()
            stats.add(seconds, size)
            processed += 1
            count += 1
            if processed >= 5 and stats.sizes:
                projected = sum(stats.sizes) / len(stats.sizes) * len(part) / 1e9
                ctx.progress(label, count, total, projected_phase_gb=round(projected, 1), free_gb=round(free_gb(), 1))
    idx = read_index()["sessions"]
    states = [idx.get(key_of(s["year"], s["round"], s["session"]), {}).get("status") for s in wanted]
    if all(x in (OK, NO_DATA) for x in states):
        settings.set_f1tv("off")
        ctx.progress(STATE_FINISHED, count, total, state=STATE_FINISHED)
        return {"finished": True, "sessions": total}
    ctx.progress("finished with some sessions failed", count, total, state="finished")
    return {"finished": False, "sessions": total}


# ---------------------------------------------------------------- classic job

def classic_order(index, existing):
    """Held races from ARCHIVE_FIRST_SEASON - 1 down to CLASSIC_FIRST_SEASON, newest season first, skipping existing bundles."""
    today = datetime.now(timezone.utc).date().isoformat()
    out = []
    for season in range(config.ARCHIVE_FIRST_SEASON - 1, config.CLASSIC_FIRST_SEASON - 1, -1):
        races = [r for r in index.get("seasons", {}).get(str(season), []) if r["date"] <= today]
        for r in sorted(races, key=lambda r: -int(r["round"])):
            if bundle_io.make_race_id("jolpica", season, r["round"]) not in existing:
                out.append((season, int(r["round"])))
    return out


def existing_bundles():
    return {p.name for p in bundle_io.BUNDLE_DIR.iterdir() if (p / "meta.json").exists()} if bundle_io.BUNDLE_DIR.exists() else set()


def load_library_index():
    return json.loads((ROOT / "data" / "library_index.json").read_text())


def download_and_build_classic(season, rnd):
    import build_classic
    import download_classic
    download_classic.download(season, rnd)
    return build_classic.build(season, rnd)


def run_classic(ctx, params=None):
    index = load_library_index()
    todo = classic_order(index, existing_bundles())
    total_all = sum(1 for s in range(config.CLASSIC_FIRST_SEASON, config.ARCHIVE_FIRST_SEASON)
                    for r in index.get("seasons", {}).get(str(s), []) if r["date"] <= datetime.now(timezone.utc).date().isoformat())
    done = total_all - len(todo)
    errors = []
    stats = Stats()
    for season, rnd in todo:
        if ctx.cancelled():
            return None
        if free_gb() < config.ARCHIVE_STOP_FREE_GB:
            raise jobs.JobPaused("disk almost full")
        label = f"{season} round {rnd:02d}"
        eta = stats.average() * (total_all - done) if len(stats.seconds) >= config.AUTO_ARCHIVE_CONFIRM_SESSIONS else None
        ctx.progress(label, done, total_all, current=label, state="running", average_s=stats.average(), eta_s=eta,
                     free_gb=round(free_gb(), 1), errors=errors[-5:])
        started = time.time()
        try:
            download_and_build_classic(season, rnd)
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
        stats.add(time.time() - started, 0)
        done += 1
    ctx.progress("finished", done, total_all, state="finished", errors=errors[-5:])
    return {"finished": True, "errors": len(errors)}


class ConsoleContext:
    """Stand-in for the job context when archive.py runs from the command line."""

    def progress(self, text, done=None, total=None, **extra):
        print(f"[{done if done is not None else '-'}/{total if total is not None else '-'}] {text}", flush=True)

    def cancelled(self):
        return False

    def sleep(self, seconds):
        time.sleep(seconds)
        return True


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Run the archive or the classic download from the command line.")
    ap.add_argument("--scope", choices=["races_sprints", "classic"], required=True)
    args = ap.parse_args()
    ctx = ConsoleContext()
    result = run_archive(ctx) if args.scope == "races_sprints" else run_classic(ctx)
    print(result)


if __name__ == "__main__":
    main()
