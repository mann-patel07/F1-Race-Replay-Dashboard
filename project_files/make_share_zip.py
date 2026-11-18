"""Write the viewing copy: project_files/share/F1_Dashboard_<YYYYMMDD>.zip

The copy holds click_here.html, project_files/site/, README.md and LICENSE. No Python, no bundles, no archive, no code.
python make_share_zip.py [--list] [--races of_9920,jl_2012_01]"""
import argparse
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import site_data

PROJECT = Path(__file__).resolve().parent
TOP = "F1 Dashboard"
EXTRA_FREE_GB = 2
LOCK_NAME = "update.lock"
SKIP_NAMES = {".DS_Store", "downsample.flag"}


class ShareRefused(Exception):
    pass


def collect(project, root, races=None):
    """(source path, name inside the top folder) for every file of the viewing copy."""
    project, root = Path(project), Path(root)
    prefix = "project_files/" if project != root else ""
    items = [(root / "click_here.html", "click_here.html")]
    site = project / "site"
    for p in sorted(site.rglob("*")):
        if not p.is_file() or p.name in SKIP_NAMES:
            continue
        rel = p.relative_to(site).as_posix()
        if races is not None and rel.startswith("data/races/"):
            if rel.split("/")[2] not in races:
                continue
        if races is not None and rel == "data/library.js":
            continue
        items.append((p, f"{prefix}site/{rel}"))
    for f in ("README.md", "LICENSE"):
        if (project / f).exists():
            items.append((project / f, prefix + f))
    if project != root:                                      # version 4.2: README.md and LICENSE of the top folder
        for f in ("README.md", "LICENSE"):
            if (root / f).exists():
                items.append((root / f, f))
    return [(s, n) for s, n in items if Path(s).exists()]


def library_text(project, races):
    index = json.loads((Path(project) / "data" / "library_index.json").read_text())
    payload = site_data.library_payload(index, Path(project) / "site" / "data" / "races", ready=set(races))
    return site_data.js_put("_library", "index", payload)


def write_zip(items, target, extra_text=None, prefix=""):
    tmp = Path(target).with_name("_tmp.zip")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for src, name in items:
            info = zipfile.ZipInfo(f"{TOP}/{name}", date_time=datetime.fromtimestamp(Path(src).stat().st_mtime).timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, Path(src).read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        if extra_text is not None:
            info = zipfile.ZipInfo(f"{TOP}/{prefix}site/data/library.js", date_time=datetime.now().timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, extra_text, compress_type=zipfile.ZIP_DEFLATED)
    tmp.replace(target)
    return target


def make(races=None, project=None, root=None, today=None, list_only=False, called_by_update=False):
    project = Path(project or PROJECT)
    root = Path(root or (project.parent if project.name == "project_files" else project))
    if not called_by_update and (project / "logs" / LOCK_NAME).exists():
        raise ShareRefused("update_data.py is running, wait for it to finish")
    ids = None
    if races:
        ids = set(races)
    items = collect(project, root, ids)
    if list_only:
        return items
    total = sum(Path(s).stat().st_size for s, _ in items)
    free = shutil.disk_usage(str(project)).free
    need = total + EXTRA_FREE_GB * 1e9
    if free < need:
        raise ShareRefused(f"free disk space is {free / 1e9:.1f} GB, the copy needs about {need / 1e9:.1f} GB")
    out_dir = project / "share"
    out_dir.mkdir(exist_ok=True)
    target = out_dir / f"F1_Dashboard_{(today or datetime.now()).strftime('%Y%m%d')}.zip"
    prefix = "project_files/" if project != root else ""
    extra = library_text(project, ids) if ids is not None else None
    return write_zip(items, target, extra, prefix)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--races", help="comma separated race ids")
    a = ap.parse_args()
    races = [r for r in a.races.split(",") if r] if a.races else None
    try:
        if a.list:
            for src, name in make(races=races, list_only=True):
                print(f"{Path(src).stat().st_size:>12}  {name}")
            return
        print(make(races=races))
    except ShareRefused as exc:
        sys.exit(f"refused: {exc}")


if __name__ == "__main__":
    main()
