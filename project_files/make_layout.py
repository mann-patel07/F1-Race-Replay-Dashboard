"""Put the folder in its final shape: click_here.html and one folder project_files.

python3 make_layout.py   (safe to run again: it does nothing when the layout is already in place)"""
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOLDER = "project_files"
STAY = {".git", ".github", FOLDER, "click_here.html"}
REPLACED = ("F1 Dashboard.app", "Start F1 Dashboard.bat", "click_here.app", "click_here_windows.bat")
PREFIX = f"../{FOLDER}/"
LINK = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")


def migrate(root):
    """Move every root item (hidden ones too) into project_files, except the ones that stay. Returns the moved names."""
    root = Path(root)
    target = root / FOLDER
    for name in REPLACED:
        p = root / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    moved = []
    for item in sorted(root.iterdir()):
        if item.name in STAY:
            continue
        target.mkdir(exist_ok=True)
        shutil.move(str(item), str(target / item.name))
        moved.append(item.name)
    return moved


def rewrite_readme(text):
    """Give every relative link and image path the prefix ../project_files/. Links starting with http, # or / stay."""
    def fix(m):
        path = m.group(2)
        if path.startswith(("http", "#", "/")):
            return m.group(0)
        return f"{m.group(1)}{PREFIX}{path}{m.group(3)}"
    return LINK.sub(fix, text)


def write_github_readme(root):
    root = Path(root)
    src = root / FOLDER / "README.md"
    if not src.exists():
        return None
    out = root / ".github" / "README.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(rewrite_readme(src.read_text()))
    return out


def main():
    root = HERE.parent if HERE.name == FOLDER else HERE
    moved = migrate(root)
    readme = write_github_readme(root)
    print(f"moved: {moved}" if moved else "layout already in place")
    if readme:
        print(f"wrote {readme}")


if __name__ == "__main__":
    main()
