"""F1TV sign-in helpers. This module never reads, prints, stores or passes a username or password.

FastF1 is imported only inside functions that run in a child process, never at the top of this file.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent
SECRET_VARS = ("F1_USERNAME", "F1_PASSWORD")

PROBE_CODE = """
import json, sys
import fastf1
s = fastf1.get_session({year}, {rnd}, 'Race')
s.load(laps=True, telemetry=False, weather=False, messages=False)
print(json.dumps({{"n": int(len(s.laps))}}))
"""


def clean_env():
    """Environment for every FastF1 process: no stored password variables."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_VARS}
    return env


def venv_python():
    if sys.platform == "win32":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def child_python():
    p = venv_python()
    return str(p) if p.exists() else sys.executable


def run_clean(args, timeout, cwd=None):
    """Run a child process with the cleaned environment and closed standard input."""
    return subprocess.run(args, env=clean_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=timeout, cwd=str(cwd or ROOT))


def status_text():
    try:
        r = run_clean([child_python(), "-m", "fastf1", "auth", "f1tv", "--status"], config.PROBE_TIMEOUT_S)
        return (r.stdout + r.stderr).strip()
    except Exception as exc:
        return f"status command failed: {exc}"


def probe():
    """Functional probe in a subprocess. The functional result decides."""
    text = status_text()
    try:
        code = PROBE_CODE.format(year=config.PROBE_YEAR, rnd=config.PROBE_ROUND)
        r = run_clean([child_python(), "-c", code], config.PROBE_TIMEOUT_S)
        last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        n = json.loads(last)["n"] if last.startswith("{") else 0
        if r.returncode == 0 and n > 0:
            return {"signed_in": True, "status_text": text, "detail": f"loaded {n} laps"}
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["no output"]
        return {"signed_in": False, "status_text": text, "detail": f"probe failed: {tail[0][:200]}"}
    except subprocess.TimeoutExpired:
        return {"signed_in": False, "status_text": text, "detail": "probe timed out"}
    except Exception as exc:
        return {"signed_in": False, "status_text": text, "detail": f"probe error: {exc}"}


def forget():
    r = run_clean([child_python(), "-m", "fastf1", "auth", "f1tv", "--clear"], config.PROBE_TIMEOUT_S)
    return r.returncode == 0


def terminal_signin_command():
    return f"cd '{ROOT}' && .venv/bin/python -m fastf1 auth f1tv --authenticate"
