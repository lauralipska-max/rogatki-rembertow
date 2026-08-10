import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parent
DATA = PROJECT / "data"
OUTPUT = DATA / "wszystkie_pociagi.json"
META = DATA / "schedule_meta.json"

WARSAW = ZoneInfo("Europe/Warsaw")

api_key = os.environ.get("PLK_API_KEY")

if not api_key:
    raise SystemExit("Brak PLK_API_KEY")

today = datetime.now(WARSAW).date().isoformat()

if OUTPUT.exists() and META.exists():
    try:
        meta = json.loads(META.read_text(encoding="utf-8"))
        if meta.get("date") == today:
            print("Rozkład na dziś jest już zapisany.")
            raise SystemExit(0)
    except json.JSONDecodeError:
        pass

params = {
    "dateFrom": today,
    "dateTo": today,
    "carriersInclude": "IC,KM,SKM",
    "page": 1,
    "pageSize": 10000,
}

url = (
    "https://pdp-api.plk-sa.pl/api/v1/schedules?"
    + urllib.parse.urlencode(params)
)

request = urllib.request.Request(
    url,
    headers={
        "X-API-Key": api_key,
        "Accept": "application/json",
    },
)

print("Pobieram rozkład na", today)

with urllib.request.urlopen(request, timeout=120) as response:
    raw = response.read()

DATA.mkdir(parents=True, exist_ok=True)
OUTPUT.write_bytes(raw)

META.write_text(
    json.dumps({"date": today}, indent=2),
    encoding="utf-8",
)

print("✓ Rozkład zapisany")
