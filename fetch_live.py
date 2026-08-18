import os
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
DATA = PROJECT / "data"
OUTPUT = DATA / "operations_live.json"
STATIONS_PATH = DATA / "stacje_plk.json"

import json

api_key = os.environ.get("PLK_API_KEY")

if not api_key:
    raise SystemExit("Brak PLK_API_KEY")

stations_data = json.loads(
    STATIONS_PATH.read_text(encoding="utf-8")
)

name_to_id = {
    station["name"]: int(station["id"])
    for station in stations_data.get("stations", [])
}

station_ids = ",".join(
    str(name_to_id[name])
    for name in [
        "Warszawa Rembertów",
        "Warszawa Wschodnia",
        "Mińsk Mazowiecki",
    ]
)

params = {
    "stations": station_ids,
    "carriersInclude": "IC,KM,SKM",
    "fullRoutes": "false",
    "withPlanned": "true",
    "page": 1,
    "pageSize": 10000,
}

url = (
    "https://pdp-api.plk-sa.pl/api/v1/operations?"
    + urllib.parse.urlencode(params)
)

request = urllib.request.Request(
    url,
    headers={
        "X-API-Key": api_key,
        "Accept": "application/json",
    },
)

with urllib.request.urlopen(request, timeout=60) as response:
    raw = response.read()

DATA.mkdir(parents=True, exist_ok=True)
OUTPUT.write_bytes(raw)
