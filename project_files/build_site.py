"""Convert replay bundles into the website's data files (site/data/races/<race id>/*.js) and the library.

python build_site.py [--all] [--race RACE_ID]
Never imports fastf1 and never uses the network."""
import argparse
import json
import shutil
import sys
from pathlib import Path

import bundle_io
import site_data

ROOT = Path(__file__).resolve().parent
RACES_DIR = site_data.RACES_DIR
FLAG = site_data.SITE / "data" / "downsample.flag"
PROJECTION_AFTER = 3
PROJECTION_LIMIT_BYTES = 8 * 1000 ** 3
LAP_ONLY_PROJECTED_BYTES = 150 * 1000


def bundle_folders(base=None):
    base = Path(base or bundle_io.BUNDLE_DIR)
    return sorted(p for p in base.iterdir() if (p / "meta.json").exists() and not p.name.startswith("_tmp"))


def race_id_for(folder):
    meta = json.loads((Path(folder) / "meta.json").read_text())
    return meta.get("race_id") or bundle_io.parse_race_id(Path(folder).name)["race_id"], meta


def newest_mtime(folder):
    return max((p.stat().st_mtime for p in Path(folder).rglob("*") if p.is_file()), default=0.0)


def is_stale(folder, race_id, races_dir=None):
    marker = Path(races_dir or RACES_DIR) / race_id / "meta.js"
    return (not marker.exists()) or marker.stat().st_mtime < newest_mtime(folder)


def write_race(race_id, files, races_dir=None):
    races_dir = Path(races_dir or RACES_DIR)
    tmp = races_dir / f"_tmp_{race_id}"
    final = races_dir / race_id
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for name, text in files.items():
        (tmp / name).write_text(text, encoding="utf-8", newline="\n")
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    return final


def projected_total(measured, full_on_disk, lap_only_on_disk):
    """Projected site size for every bundle on disk from the first measured FastF1 races."""
    if not measured:
        return 0
    average = sum(measured) / len(measured)
    return int(average * full_on_disk + LAP_ONLY_PROJECTED_BYTES * lap_only_on_disk)


def build(race_filter=None, rebuild_all=False, bundles=None, races_dir=None, log=print, limit=None):
    races_dir = Path(races_dir or RACES_DIR)
    races_dir.mkdir(parents=True, exist_ok=True)
    folders = bundle_folders(bundles)
    full_count = lap_count = 0
    for f in folders:
        kind = json.loads((f / "meta.json").read_text()).get("kind", "full")
        full_count += kind == "full"
        lap_count += kind != "full"
    built, measured, downsample = [], [], FLAG.exists()
    for folder in folders:
        race_id, meta = race_id_for(folder)
        if race_filter and race_id != race_filter:
            continue
        if not (rebuild_all or race_filter or is_stale(folder, race_id, races_dir)):
            continue
        bundle = bundle_io.load_bundle(folder.name, base=folder.parent)
        files = site_data.race_files(bundle, race_id, downsample, limit or site_data.MAX_FILE_BYTES)
        out = write_race(race_id, files, races_dir)
        size = site_data.folder_bytes(out)
        built.append(race_id)
        log(f"built {race_id}: {size / 1e6:.1f} MB")
        if meta.get("source") == "fastf1" and meta.get("kind", "full") == "full" and not downsample:
            measured.append(size)
            if len(measured) == PROJECTION_AFTER:
                total = projected_total(measured, full_count, lap_count)
                log(f"size projection from the first {PROJECTION_AFTER} FastF1 races: {total / 1e9:.2f} GB for {full_count} full and {lap_count} lap-only bundles")
                if total > PROJECTION_LIMIT_BYTES:
                    downsample = True
                    FLAG.parent.mkdir(parents=True, exist_ok=True)
                    FLAG.write_text("every second car_data and location sample per driver is kept\n")
                    log("projection is above 8 GB: keeping every second car_data and location sample, rebuilding the first 3")
                    for rid in built:
                        f2 = next(p for p in folders if race_id_for(p)[0] == rid)
                        b2 = bundle_io.load_bundle(f2.name, base=f2.parent)
                        write_race(rid, site_data.race_files(b2, rid, True, limit or site_data.MAX_FILE_BYTES), races_dir)
    index_path = ROOT / "data" / "library_index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"seasons": {}}
    site_data.write_library(index, races_dir)
    site_data.write_constants()
    return built


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="rebuild every race")
    ap.add_argument("--race", help="build one race id")
    args = ap.parse_args()
    built = build(args.race, args.all)
    print(f"{len(built)} races built")


if __name__ == "__main__":
    main()
