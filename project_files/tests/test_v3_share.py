import json
import zipfile
from pathlib import Path

import pytest

import make_share_zip as sz


def build_tree(base, lock=False):
    root = Path(base)
    proj = root / "project_files"
    files = {
        "README.md": "readme", "LICENSE": "mit", "run.py": "x = 1", "NOTES_FOR_MANN.md": "private", "RUN_LOG.md": "log",
        "site/style.css": "css", "site/js/app.js": "js", "site/data/library.js": 'F1DATA.put("_library", "index", {"seasons": {}});',
        "site/data/races/of_9920/meta.js": "m", "site/data/races/of_9920/laps.js": "l",
        "site/data/races/jl_2012_01/meta.js": "m", "site/data/races/ff_2021_01_R/meta.js": "m",
        "site/data/downsample.flag": "x", "archive/a.parquet": "a", "data/bundle/9920/meta.json": "{}", "logs/server.log": "l",
        "data/library_index.json": json.dumps({"seasons": {}}),
    }
    for name, body in files.items():
        p = proj / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    (root / "click_here.html").write_text("<html>")
    (root / "README.md").write_text("top readme")      # version 4.2: top folder README and LICENSE
    (root / "LICENSE").write_text("top licence")
    if lock:
        (proj / "logs" / "update.lock").write_text("now")
    return root, proj


def test_viewing_copy_holds_only_the_site(tmp_path):
    root, proj = build_tree(tmp_path)
    target = sz.make(project=proj, root=root)
    names = zipfile.ZipFile(target).namelist()
    assert target.parent == proj / "share" and not (proj / "share" / "_tmp.zip").exists()
    assert "F1 Dashboard/click_here.html" in names and "F1 Dashboard/project_files/README.md" in names
    assert "F1 Dashboard/project_files/LICENSE" in names and "F1 Dashboard/project_files/site/js/app.js" in names
    assert "F1 Dashboard/project_files/site/data/races/of_9920/laps.js" in names
    top = {n.split("/", 2)[1] for n in names}
    assert top == {"click_here.html", "project_files", "README.md", "LICENSE"}   # version 4.2: top folder README and LICENSE
    joined = "\n".join(names)
    for gone in ("run.py", "NOTES", "RUN_LOG", "archive/", "data/bundle", "logs/", "downsample.flag", "library_index"):
        assert gone not in joined, gone
    z = zipfile.ZipFile(target)
    assert z.getinfo("F1 Dashboard/project_files/site/js/app.js").compress_type == zipfile.ZIP_DEFLATED


def test_races_option_limits_the_races_and_the_library(tmp_path):
    root, proj = build_tree(tmp_path)
    target = sz.make(races=["of_9920"], project=proj, root=root)
    z = zipfile.ZipFile(target)
    names = z.namelist()
    assert any("of_9920" in n for n in names) and not any("jl_2012_01" in n or "ff_2021_01_R" in n for n in names)
    assert "F1DATA.put(\"_library\"" in z.read("F1 Dashboard/project_files/site/data/library.js").decode()


def test_list_writes_nothing_and_refuses_while_update_runs(tmp_path):
    root, proj = build_tree(tmp_path)
    items = sz.make(project=proj, root=root, list_only=True)
    assert items and not (proj / "share").exists()
    root2, proj2 = build_tree(tmp_path / "two", lock=True)
    with pytest.raises(sz.ShareRefused):
        sz.make(project=proj2, root=root2)
    assert not (proj2 / "share").exists()


def test_refuses_when_the_disk_is_too_small(tmp_path, monkeypatch):
    root, proj = build_tree(tmp_path)
    monkeypatch.setattr(sz.shutil, "disk_usage", lambda p: type("U", (), {"free": 1000})())
    with pytest.raises(sz.ShareRefused):
        sz.make(project=proj, root=root)
