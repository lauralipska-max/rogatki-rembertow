import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT = Path(__file__).resolve().parent
PLAN_PATH = PROJECT / "data" / "wszystkie_pociagi.json"
LIVE_PATH = PROJECT / "data" / "operations_live.json"
STATIONS_PATH = PROJECT / "data" / "stacje_plk.json"

WARSAW = ZoneInfo("Europe/Warsaw")
REMBERTOW_OFFSET = timedelta(minutes=6, seconds=30)


def find_routes(data):
    for value in data.values():
        if (
            isinstance(value, list)
            and value
            and isinstance(value[0], dict)
            and "stations" in value[0]
        ):
            return value
    return []


def station_id(station):
    try:
        return int(station.get("stationId"))
    except (TypeError, ValueError):
        return None


def parse_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is not None:
            return parsed.astimezone(WARSAW).replace(tzinfo=None)

        return parsed

    except ValueError:
        return None


def sequence_number(station):
    return (
        station.get("plannedSequenceNumber")
        or station.get("actualSequenceNumber")
        or 0
    )


def latest_known_delay(stations, target_sequence):
    candidates = []

    for station in stations:
        sequence = sequence_number(station)

        if sequence > target_sequence:
            continue

        delay = station.get("departureDelayMinutes")

        if delay is None:
            delay = station.get("arrivalDelayMinutes")

        has_actual = (
            station.get("actualArrival")
            or station.get("actualDeparture")
            or station.get("isConfirmed")
        )

        if delay is not None and has_actual:
            candidates.append((sequence, int(delay)))

    if not candidates:
        return 0, False

    candidates.sort()
    return candidates[-1][1], True


def predicted_station_time(station, stations, event_type):
    if event_type == "departure":
        actual = parse_datetime(station.get("actualDeparture"))
        planned = parse_datetime(station.get("plannedDeparture"))
        delay = station.get("departureDelayMinutes")
    else:
        actual = parse_datetime(station.get("actualArrival"))
        planned = parse_datetime(station.get("plannedArrival"))
        delay = station.get("arrivalDelayMinutes")

    if station.get("isConfirmed") and actual:
        return actual, "wykonanie potwierdzone"

    if not planned:
        return None, "brak danych"

    if delay is not None:
        return planned + timedelta(minutes=int(delay)), f"opóźnienie {int(delay):+d} min"

    inherited_delay, found = latest_known_delay(
        stations,
        sequence_number(station),
    )

    if found:
        return (
            planned + timedelta(minutes=inherited_delay),
            f"ostatnie znane opóźnienie {inherited_delay:+d} min",
        )

    return planned, "sam plan"


def predicted_rembertow_stop(station, stations):
    arrival, arrival_source = predicted_station_time(
        station,
        stations,
        "arrival",
    )
    departure, departure_source = predicted_station_time(
        station,
        stations,
        "departure",
    )

    if arrival and departure:
        return (
            arrival + (departure - arrival) / 2,
            departure_source
            if "opóźnienie" in departure_source
            else arrival_source,
        )

    if arrival:
        return arrival, arrival_source

    if departure:
        return departure, departure_source

    return None, "brak danych"


plan_data = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
live_data = json.loads(LIVE_PATH.read_text(encoding="utf-8"))
stations_data = json.loads(STATIONS_PATH.read_text(encoding="utf-8"))

plan_routes = find_routes(plan_data)
live_trains = live_data.get("trains", [])

station_names = {
    int(item["id"]): item["name"]
    for item in stations_data.get("stations", [])
}

name_to_id = {
    name: station_id_value
    for station_id_value, name in station_names.items()
}

REMBERTOW_ID = name_to_id["Warszawa Rembertów"]
WSCHODNIA_ID = name_to_id["Warszawa Wschodnia"]

corridor_names = {
    "Warszawa Wesoła",
    "Warszawa Wola Grzybowska",
    "Sulejówek",
    "Sulejówek Miłosna",
    "Halinów",
    "Cisie",
    "Dębe Wielkie",
    "Nowe Dębe Wielkie",
    "Mińsk Mazowiecki",
    "Mińsk Mazowiecki Anielina",
    "Mrozy",
    "Siedlce",
    "Łuków",
    "Biała Podlaska",
    "Terespol",
    "Zielonka",
    "Wołomin",
    "Tłuszcz",
}

corridor_ids = {
    name_to_id[name]
    for name in corridor_names
    if name in name_to_id
}

plan_lookup = {}

for train in plan_routes:
    key = (train.get("scheduleId"), train.get("orderId"))

    route = sorted(
        train.get("stations", []),
        key=lambda item: item.get("orderNumber", 0),
    )

    ids = [station_id(item) for item in route]

    info = {
        "carrier": train.get("carrierCode") or "?",
        "category": train.get("commercialCategorySymbol") or "?",
        "number": train.get("nationalNumber") or "?",
        "type": None,
        "direction": None,
    }

    if REMBERTOW_ID in ids:
        rembertow_index = ids.index(REMBERTOW_ID)
        info["type"] = "direct"

        if WSCHODNIA_ID in ids:
            wschodnia_index = ids.index(WSCHODNIA_ID)
            info["direction"] = (
                "Warszawa → wschód"
                if wschodnia_index < rembertow_index
                else "wschód → Warszawa"
            )
        else:
            info["direction"] = "nieustalony"

    elif WSCHODNIA_ID in ids:
        wschodnia_index = ids.index(WSCHODNIA_ID)

        east_after = any(
            item in corridor_ids
            for item in ids[wschodnia_index + 1:]
        )

        east_before = any(
            item in corridor_ids
            for item in ids[:wschodnia_index]
        )

        if east_after:
            info["type"] = "passing"
            info["direction"] = "Warszawa → wschód"

        elif east_before:
            info["type"] = "passing"
            info["direction"] = "wschód → Warszawa"

    if info["type"]:
        plan_lookup[key] = info


status_names = {
    "S": "jeszcze nie ruszył",
    "P": "w trasie",
    "C": "zakończony",
    "X": "odwołany",
    "Q": "częściowo odwołany",
}

status_counts = Counter(
    train.get("trainStatus") or "?"
    for train in live_trains
)

events = []

for train in live_trains:
    key = (train.get("scheduleId"), train.get("orderId"))
    info = plan_lookup.get(key)

    if not info:
        continue

    status = train.get("trainStatus")

    if status in {"X", "C"}:
        continue

    stations = train.get("stations") or []
    station_lookup = {
        station_id(station): station
        for station in stations
    }

    predicted = None
    source = None

    if info["type"] == "direct":
        rembertow = station_lookup.get(REMBERTOW_ID)

        if rembertow and not rembertow.get("isCancelled"):
            predicted, source = predicted_rembertow_stop(
                rembertow,
                stations,
            )

    elif info["type"] == "passing":
        wschodnia = station_lookup.get(WSCHODNIA_ID)

        if not wschodnia or wschodnia.get("isCancelled"):
            continue

        if info["direction"] == "Warszawa → wschód":
            base_time, source = predicted_station_time(
                wschodnia,
                stations,
                "departure",
            )

            if base_time:
                predicted = base_time + REMBERTOW_OFFSET

        else:
            base_time, source = predicted_station_time(
                wschodnia,
                stations,
                "arrival",
            )

            if base_time:
                predicted = base_time - REMBERTOW_OFFSET

    if not predicted:
        continue

    events.append({
        "time": predicted,
        "carrier": info["carrier"],
        "category": info["category"],
        "number": info["number"],
        "direction": info["direction"],
        "type": info["type"],
        "status": status_names.get(status, status or "?"),
        "source": source,
    })


events.sort(key=lambda item: item["time"])

now = datetime.now()
upcoming = [
    event
    for event in events
    if event["time"] >= now - timedelta(minutes=2)
][:25]

generated_at = live_data.get("generatedAt")
pagination = live_data.get("pagination")

output_events = []

for event in events:
    output_events.append({
        "time": event["time"].isoformat(),
        "carrier": event["carrier"],
        "category": event["category"],
        "number": event["number"],
        "direction": event["direction"],
        "type": event["type"],
        "status": event["status"],
        "source": event["source"],
    })

output_path = PROJECT / "data" / "live_events.json"
output_path.write_text(
    json.dumps(
        output_events,
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)

print("DANE LIVE")
print("Wygenerowane przez API:", generated_at)
print("Paginacja:", pagination)
print("Wszystkie rekordy operations:", len(live_trains))

print("\nSTATUSY WSZYSTKICH REKORDÓW")
for status, count in sorted(status_counts.items()):
    print(f"- {status}: {count} ({status_names.get(status, '?')})")

print("\nDOPASOWANE PRZEJAZDY PRZEZ REMBERTÓW:", len(events))
print("\nNAJBLIŻSZE PRZEJAZDY LIVE\n")

for event in upcoming:
    passing_label = (
        "bez postoju"
        if event["type"] == "passing"
        else "postój Rembertów"
    )

    print(
        f"{event['time']:%H:%M:%S} | "
        f"{event['carrier']} {event['category']} {event['number']} | "
        f"{event['direction']} | "
        f"{passing_label} | "
        f"{event['status']} | "
        f"{event['source']}"
    )
