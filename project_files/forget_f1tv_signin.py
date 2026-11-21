"""Remove the saved F1TV sign-in from this Mac, but only when both downloads are finished.

Run it by double-clicking forget_f1tv_signin.command. The app never runs it."""
import subprocess
import sys
from pathlib import Path

import archive
import f1_auth
import settings
import update_data

ROOT = Path(__file__).resolve().parent
TELEMETRY_MARKERS = [ROOT / ".." / ".." / "f1 telemetry" / "data" / "DOWNLOAD_COMPLETE.json",
                     ROOT / ".." / ".." / "f1 telemetry" / "project_files" / "data" / "DOWNLOAD_COMPLETE.json"]


def refusal_reasons(index=None, archive_index=None, markers=None, now=None):
    """Why the sign-in must not be removed yet. Empty list means it may be removed."""
    index = index if index is not None else update_data.load_index()
    archive_index = archive_index if archive_index is not None else archive.read_index()
    markers = TELEMETRY_MARKERS if markers is None else markers
    reasons = []
    left = update_data.missing_f1tv_sessions(index, archive_index, now)
    if left:
        reasons.append(f"{len(left)} races or sprints are not downloaded yet. Run update_data.command first.")
    if not any(Path(m).exists() for m in markers):
        reasons.append("The F1 telemetry project has not written its DOWNLOAD_COMPLETE.json file yet.")
    return reasons


def clear_supported():
    r = f1_auth.run_clean([f1_auth.child_python(), "-m", "fastf1", "auth", "f1tv", "--help"], 60)
    return "--clear" in (r.stdout + r.stderr)


def main():
    reasons = refusal_reasons()
    if reasons:
        print("Not removing the sign-in:")
        for r in reasons:
            print(" -", r)
        return 1
    answer = input("Remove the saved F1TV sign-in from this Mac? Type yes and press Enter. ").strip().lower()
    if answer != "yes":
        print("Nothing was changed.")
        return 1
    if clear_supported():
        f1_auth.forget()
    else:
        print("This FastF1 version has no --clear option, so nothing was removed.")
    settings.set_f1tv("off")
    print("Done. You can change your F1 password now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
