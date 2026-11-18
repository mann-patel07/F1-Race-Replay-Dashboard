from pathlib import Path

import make_layout

ROOT = Path(__file__).resolve().parent.parent
HOME_MARK = "/Us" + "ers/"


def test_layout_moves_everything_and_second_run_changes_nothing(tmp_path):
    for name in (".git", ".github", ".venv", "Claude outputs", "data", "web", "click_here.app", "F1 Dashboard.app"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "f.txt").write_text(name)
    for name in ("run.py", "README.md", ".gitignore", ".DS_Store", "click_here.html", "click_here_windows.bat", "Start F1 Dashboard.bat"):
        p = tmp_path / name
        p.write_text(name)
    moved = make_layout.migrate(tmp_path)
    assert set(p.name for p in tmp_path.iterdir()) == {".git", ".github", "project_files", "click_here.html"}
    inside = {p.name for p in (tmp_path / "project_files").iterdir()}
    assert {".venv", "Claude outputs", "data", "web", "run.py", "README.md", ".gitignore", ".DS_Store"} <= inside
    assert "F1 Dashboard.app" not in inside and "Start F1 Dashboard.bat" not in inside and "click_here.app" not in inside
    assert (tmp_path / "project_files" / "data" / "f.txt").read_text() == "data"
    assert (tmp_path / ".git" / "f.txt").exists() and (tmp_path / ".github" / "f.txt").exists()
    assert moved
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert make_layout.migrate(tmp_path) == []
    assert before == sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))


def test_readme_link_rewrite():
    text = ("![Start](screenshots/01_start.png)\n[docs](docs/a.md)\n[site](https://example.org/x)\n"
            "[http](http://example.org)\n[anchor](#top)\n[root](/abs/path)\n![t](shots/b.png \"title\")\n")
    out = make_layout.rewrite_readme(text)
    assert "![Start](../project_files/screenshots/01_start.png)" in out
    assert "[docs](../project_files/docs/a.md)" in out
    assert "(https://example.org/x)" in out and "(http://example.org)" in out and "(#top)" in out and "(/abs/path)" in out
    assert "../project_files/shots/b.png \"title\"" in out
    assert "../project_files/https" not in out and "../project_files/#" not in out


def test_github_readme_is_generated(tmp_path):
    (tmp_path / "project_files").mkdir()
    (tmp_path / "project_files" / "README.md").write_text("![a](screenshots/a.png)\n")
    out = make_layout.write_github_readme(tmp_path)
    assert out == tmp_path / ".github" / "README.md"
    assert out.read_text() == "![a](../project_files/screenshots/a.png)\n"


def test_no_python_file_uses_the_working_directory_to_find_data():
    bad = ("os.getcwd", "Path.cwd", "Path('.')", 'Path(".")', "open('data", 'open("data')
    for p in ROOT.glob("*.py"):
        text = p.read_text()
        for b in bad:
            assert b not in text, (p.name, b)
