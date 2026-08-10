import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from pprint import pprint


PROJECT = Path(__file__).resolve().parent
STATIONS_PATH = PROJECT / "data" / "stacje_plk.json"
OUTPUT_PATH = PROJECT / "data" / "operations_live.json"

api_key = os.environ.get("PLK_API_KEY")

if not api_key:
    raise SystemExit(
        "Brak PLK_API_KEY. Najpierw wpisz:\n"
        'read -s "PLK_API_KEY?Wklej klucz API: "\n'
        "echo"
    )

stations_data = json.loads(
    STATIONS_PATH.read_text(encoding="utf-8")
)

name_to_id = {
    station["name"]: int(station["id"])
    for station in stations_data.get("stations", [])
}

required_names = [
    "Warszawa Rembertów",
    "Warszawa Wschodnia",
    "Mińsk Mazowiecki",
]

missing = [
    name for name in required_names
    if name not in name_to_id
]

if missing:
    raise SystemExit(
        f"Brakuje stacji w słowniku: {missing}"
    )

station_ids = ",".join(
    str(name_to_id[name])
    for name in required_names
)

params = {
    "stations": station_ids,
    "carriersInclude": "IC,KM,SKM",
    "fullRoutes": "true",
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

print("Pobieram dane live...")

with urllib.request.urlopen(request, timeout=60) as response:
    print("HTTP:", response.status)
    print(
        "Pozostało zapytań w tej godzinie:",
        response.headers.get("X-RateLimit-Hourly-Remaining"),
    )
    raw = response.read()

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_bytes(raw)

data = json.loads(raw)

print("\nPola główne odpowiedzi:")

for key, value in data.items():
    if isinstance(value, list):
        print(f"- {key}: lista ({len(value)} rekordów)")
    elif isinstance(value, dict):
        print(f"- {key}: obiekt")
    else:
        print(f"- {key}: {value}")

candidate_lists = [
    (key, value)
    for key, value in data.items()
    if isinstance(value, list)
    and value
    and isinstance(value[0], dict)
]

if not candidate_lists:
    raise SystemExit(
        "\nNie znaleziono listy realizacji pociągów."
    )

list_name, operations = max(
    candidate_lists,
    key=lambda item: len(item[1]),
)

print(f"\nGłówna lista: {list_name}")
print(f"Liczba realizacji: {len(operations)}")

first = operations[0]

print("\nPola pierwszego rekordu:")
for key in first.keys():
    print("-", key)

print("\nPierwszy pełny rekord:")
pprint(first, sort_dicts=False, width=120)

interesting_fields = set()


def inspect_fields(value):
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()

            if any(
                phrase in lowered
                for phrase in (
                    "delay",
                    "planned",
                    "actual",
                    "estimated",
                    "arrival",
                    "departure",
                    "status",
                    "time",
                )
            ):
                interesting_fields.add(key)

            inspect_fields(child)

    elif isinstance(value, list):
        for child in value[:20]:
            inspect_fields(child)


for operation in operations[:20]:
    inspect_fields(operation)

print("\nPola związane z czasem i opóźnieniami:")
for field in sorted(interesting_fields):
    print("-", field)

print("\nPlik zapisany jako:")
print(OUTPUT_PATH)
