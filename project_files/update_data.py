"""Add more races to the website: python update_data.py (or double-click update_data.command).

Steps, each resumable (work already saved is skipped):
1 calendar, 2 FastF1 races and sprints (only when something is missing and F1TV downloads are on),
3 OpenF1, 4 lap-only 2007 to 2020, 5 website data after every 10 races and at the end, 7 viewing copy."""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import archive
import bundle_io
import config
import f1_auth
import settings

ROOT = Path(__file__).resolve().parent
LOCK = ROOT / "logs" / "update.lock"
LOG = ROOT / "logs" / "update.log"
INDEX_PATH = ROOT / "data" / "library_index.json"
BUILD_SITE_EVERY = 10


def load_index():
    try:
        return json.loads(INDEX_PATH.read_text())
    except (OSError, ValueError):
        return {"seasons": {}}


def missing_f1tv_sessions(index, archive_index, now=None):
    """Race and Sprint sessions from ARCHIVE_FIRST_SEASON on that are held and not ok or no_data in the archive index."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).date().isoformat()
    out = []
    for season, races in index.get("seasons", {}).items():
        if int(season) < config.ARCHIVE_FIRST_SEASON:
            continue
        for r in races:
            for name, day in (("Race", r["date"]), ("Sprint", r.get("sprint_date"))):
                if not day or day > cutoff:
                    continue
                status = archive_index.get("sessions", {}).get(archive.key_of(r["season"], r["round"], name), {}).get("status")
                if status not in (archive.OK, archive.NO_DATA):
                    out.append({"year": r["season"], "round": r["round"], "session": name, "event": r["name"]})
    return out


def missing_openf1(index, bundles, today=None):
    """Held 2023 or later races with an OpenF1 session and no full bundle (FastF1 or OpenF1)."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    out = []
    for season, races in index.get("seasons", {}).items():
        if int(season) < 2023:
            continue
        for r in races:
            key = r.get("openf1_session_key")
            if key is None or r["date"] > today:
                continue
            ff = bundle_io.make_race_id("fastf1", r["season"], r["round"], "R")
            if ff in bundles or str(key) in bundles:
                continue
            out.append({"key": key, "season": r["season"], "round": r["round"], "name": r["name"]})
    return out


def run_script(args):
    r = subprocess.run([f1_auth.child_python(), *map(str, args)], stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"{Path(str(args[0])).name} failed: {tail[0][:200]}")


class Deps:
    """Everything update_data does outside this file. Tests replace these."""

    def build_index(self):
        import build_index
        build_index.main()

    def f1tv_enabled(self):
        return settings.f1tv_enabled()

    def probe(self):
        return f1_auth.probe()

    def run_archive(self):
        return archive.run_archive(archive.ConsoleContext())

    def openf1_build(self, item):
        run_script([ROOT / "download.py", "--session-key", item["key"]])
        run_script([ROOT / "build_bundle.py", "--session", item["key"]])

    def classic_build(self, season, rnd):
        archive.download_and_build_classic(season, rnd)

    def build_site(self):
        import build_site
        build_site.build()

    def make_zip(self):
        import make_share_zip
        return make_share_zip.make(called_by_update=True)

    def existing_bundles(self):
        return archive.existing_bundles()

    def archive_index(self):
        return archive.read_index()


def update(deps=None, say=print):
    deps = deps or Deps()
    summary = {"steps": [], "built": 0, "failed": [], "f1tv": "skipped", "zip": None}
    log_lines = []

    def out(text):
        stamp = datetime.now().strftime("%H:%M:%S")
        log_lines.append(f"{stamp} {text}")
        say(text)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {text}\n")

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(datetime.now(timezone.utc).isoformat()))
    try:
        out("1 calendar")
        summary["steps"].append("index")
        try:
            deps.build_index()
        except Exception as exc:
            out(f"calendar update failed, using the saved calendar: {exc}")
        index = load_index()

        since_site = [0]

        def built_one(n, total, label):
            summary["built"] += 1
            since_site[0] += 1
            out(f"[{n}/{total}] {label}")
            if since_site[0] >= BUILD_SITE_EVERY:
                deps.build_site()
                since_site[0] = 0

        wanted = missing_f1tv_sessions(index, deps.archive_index())
        f1tv_on = deps.f1tv_enabled()
        openf1_todo_preview = missing_openf1(index, deps.existing_bundles())
        classic_todo = archive.classic_order(index, deps.existing_bundles())
        total = len(wanted if f1tv_on else []) + len(openf1_todo_preview) + len(classic_todo)

        out("2 FastF1 races and sprints")
        summary["steps"].append("fastf1")
        if not wanted:
            out("nothing missing, no FastF1 call")
        elif not f1tv_on:
            out(f"{len(wanted)} sessions missing, but F1TV downloads are off, so FastF1 is skipped")
            summary["f1tv"] = "off"
        else:
            probe = deps.probe()
            if not probe["signed_in"]:
                out(f"{len(wanted)} sessions missing, but not signed in to F1TV ({probe['detail']}): FastF1 skipped")
                summary["f1tv"] = "not signed in"
            else:
                result = deps.run_archive()
                summary["f1tv"] = "ran"
                built_one(summary["built"] + 1, max(total, 1), f"FastF1 archive finished: {result}")

        out("3 OpenF1")
        summary["steps"].append("openf1")
        for item in missing_openf1(index, deps.existing_bundles()):
            try:
                deps.openf1_build(item)
                built_one(summary["built"] + 1, max(total, 1), f"{item['season']} round {item['round']} {item['name']}: full replay ready (OpenF1)")
            except Exception as exc:
                summary["failed"].append(f"{item['season']} round {item['round']}: {exc}")
                out(f"{item['season']} round {item['round']} {item['name']}: OpenF1 failed: {exc}")

        out("4 lap-only 2007 to 2020")
        summary["steps"].append("classic")
        by_key = {(r["season"], r["round"]): r["name"] for races in index.get("seasons", {}).values() for r in races}
        for season, rnd in archive.classic_order(index, deps.existing_bundles()):
            label = f"{season} round {rnd} {by_key.get((season, rnd), '')}"
            try:
                deps.classic_build(season, rnd)
                built_one(summary["built"] + 1, max(total, 1), f"{label}: lap-only ready")
            except Exception as exc:
                summary["failed"].append(f"{label}: {exc}")
                out(f"{label}: failed: {exc}")

        out("5 website data")
        summary["steps"].append("site")
        deps.build_site()
        out("7 viewing copy")
        summary["steps"].append("zip")
        try:
            summary["zip"] = str(deps.make_zip())
            out(f"viewing copy: {summary['zip']}")
        except Exception as exc:
            out(f"viewing copy not made: {exc}")
        left = len(archive.classic_order(index, deps.existing_bundles()))
        out(f"Summary: {summary['built']} races built, {len(summary['failed'])} failed, {left} lap-only races left, FastF1 step: {summary['f1tv']}")
    finally:
        LOCK.unlink(missing_ok=True)
    return summary


def main():
    update()


if __name__ == "__main__":
    main()
