import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parent
LIVE_PATH = PROJECT / "data" / "operations_live.json"

WARSAW = ZoneInfo("Europe/Warsaw")
MAX_LIVE_AGE = timedelta(seconds=90)


def run(name):
    result = subprocess.run(
        [sys.executable, str(PROJECT / name)],
        cwd=PROJECT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=180,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr
            or result.stdout
            or f"Błąd skryptu {name}"
        )


def live_is_fresh():
    if not LIVE_PATH.exists():
        return False

    try:
        data = json.loads(
            LIVE_PATH.read_text(encoding="utf-8")
        )

        generated_at = data.get("generatedAt")

        if not generated_at:
            return False

        timestamp = datetime.fromisoformat(
            generated_at.replace("Z", "+00:00")
        ).astimezone(WARSAW)

        age = datetime.now(WARSAW) - timestamp

        return timedelta(0) <= age < MAX_LIVE_AGE

    except (OSError, ValueError, json.JSONDecodeError):
        return False


run("refresh_schedule.py")

if not live_is_fresh():
    run("fetch_live.py")

run("build_live_timeline.py")
