import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from supabase import create_client


PROJECT = Path(__file__).resolve().parent
EVENTS_PATH = PROJECT / "data" / "live_events.json"
LIVE_PATH = PROJECT / "data" / "operations_live.json"
CONFIG_PATH = PROJECT / "config.json"
STYLES_PATH = PROJECT / "styles.css"

WARSAW = ZoneInfo("Europe/Warsaw")
DATA_MAX_AGE = timedelta(seconds=90)

st.set_page_config(
    page_title="Rogaty Rembertów",
    page_icon="🚧",
    layout="centered",
)


def load_styles():
    if STYLES_PATH.exists():
        css = STYLES_PATH.read_text(encoding="utf-8")
        st.html(f"<style>{css}</style>")


def load_config():
    default = {
        "close_before_seconds": 210,
        "open_after_seconds": 60,
        "minimum_opening_seconds": 60,
    }

    if not CONFIG_PATH.exists():
        return default

    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {**default, **saved}
    except (OSError, json.JSONDecodeError):
        return default


@st.cache_data(ttl=75, show_spinner=False)
def refresh_data():
    if not os.environ.get("PLK_API_KEY") and not st.secrets.get("PLK_API_KEY", ""):
        raise RuntimeError(
            "Brak klucza API. Dodaj go do secrets lub zmiennej środowiskowej."
        )

    scripts = [
        PROJECT / "refresh_pipeline.py",
    ]

    outputs = []

    env = os.environ.copy()
    if st.secrets.get("PLK_API_KEY"):
        env["PLK_API_KEY"] = st.secrets["PLK_API_KEY"]

    for script in scripts:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=PROJECT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

        outputs.append(result.stdout)

        if result.returncode != 0:
            error = result.stderr or result.stdout
            raise RuntimeError(error)

    return "\n".join(outputs)


def load_events():
    if not EVENTS_PATH.exists():
        return []

    try:
        data = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    events = []

    for item in data:
        try:
            passage_time = datetime.fromisoformat(item["time"])
        except (KeyError, TypeError, ValueError):
            continue

        events.append({
            **item,
            "passage_time": passage_time,
        })

    return sorted(events, key=lambda event: event["passage_time"])


def build_closures(events, config):
    close_before = timedelta(seconds=config["close_before_seconds"])
    open_after = timedelta(seconds=config["open_after_seconds"])

    closures = []

    for event in events:
        closed_from = event["passage_time"] - close_before
        closed_until = event["passage_time"] + open_after

        if not closures:
            closures.append({
                "from": closed_from,
                "until": closed_until,
                "trains": [event],
            })
            continue

        previous = closures[-1]

        if closed_from <= previous["until"]:
            previous["until"] = max(previous["until"], closed_until)
            previous["trains"].append(event)
        else:
            closures.append({
                "from": closed_from,
                "until": closed_until,
                "trains": [event],
            })

    return closures


def get_data_timestamp():
    if not LIVE_PATH.exists():
        return None

    try:
        data = json.loads(LIVE_PATH.read_text(encoding="utf-8"))
        generated_at = data.get("generatedAt")

        if not generated_at:
            return None

        parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        return parsed.astimezone(WARSAW).replace(tzinfo=None)
    except (OSError, json.JSONDecodeError, ValueError):
        return None



def data_is_stale():
    timestamp = get_data_timestamp()
    if timestamp is None:
        return True
    now = datetime.now(WARSAW).replace(tzinfo=None)
    return (now - timestamp) >= DATA_MAX_AGE


def save_model_history(status_class, next_closure, timestamp, upcoming_events):
    try:
        supabase_url = st.secrets.get("SUPABASE_URL", "")
        supabase_key = st.secrets.get("SUPABASE_SECRET_KEY", "")

        if not supabase_url or not supabase_key:
            return

        client = create_client(supabase_url, supabase_key)

        next_close = None
        next_open = None

        if next_closure:
            next_close = next_closure["from"].replace(
                tzinfo=WARSAW
            ).isoformat()
            next_open = next_closure["until"].replace(
                tzinfo=WARSAW
            ).isoformat()

        trains = [
            {
                "time": event["passage_time"].isoformat(),
                "carrier": event.get("carrier"),
                "category": event.get("category"),
                "number": event.get("number"),
                "direction": event.get("direction"),
            }
            for event in upcoming_events[:10]
        ]

        client.table("model_history").upsert({
            "model_status": status_class.upper(),
            "next_close": next_close,
            "next_open": next_open,
            "source_generated_at": (
                timestamp.replace(tzinfo=WARSAW).isoformat()
                if timestamp
                else None
            ),
            "trains": trains,
        }, on_conflict="source_generated_at").execute()

    except Exception as error:
        print(
            f"Nie udało się zapisać historii modelu: {error}",
            file=sys.stderr,
        )

def format_duration(duration):
    total_seconds = max(0, int(duration.total_seconds()))
    minutes, seconds = divmod(total_seconds, 60)

    if minutes:
        return f"{minutes} min {seconds} s"

    return f"{seconds} s"


def build_openings(future_closures, minimum_opening):
    openings = []

    for current, following in zip(future_closures, future_closures[1:]):
        open_from = current["until"]
        open_until = following["from"]
        duration = open_until - open_from

        if duration >= minimum_opening:
            openings.append({
                "from": open_from,
                "until": open_until,
                "duration": duration,
            })

    return openings


def build_timeline_segments(now, future_closures, horizon_minutes=60):
    horizon_end = now + timedelta(minutes=horizon_minutes)
    segments = []
    cursor = now

    relevant_closures = [
        closure for closure in future_closures
        if closure["until"] > now and closure["from"] < horizon_end
    ]

    for closure in relevant_closures:
        start = max(closure["from"], now)
        end = min(closure["until"], horizon_end)

        if start > cursor:
            segments.append({
                "status": "open",
                "start": cursor,
                "end": start,
            })

        if end > start:
            segments.append({
                "status": "closed",
                "start": start,
                "end": end,
            })

        cursor = max(cursor, end)

    if cursor < horizon_end:
        segments.append({
            "status": "open",
            "start": cursor,
            "end": horizon_end,
        })

    total_seconds = max(1, int((horizon_end - now).total_seconds()))

    enriched = []
    for segment in segments:
        duration_seconds = max(1, int((segment["end"] - segment["start"]).total_seconds()))
        width_percent = (duration_seconds / total_seconds) * 100
        enriched.append({
            **segment,
            "width_percent": width_percent,
        })

    return enriched, horizon_end


def render_timeline(now, future_closures):
    segments, horizon_end = build_timeline_segments(now, future_closures, 60)

    if not segments:
        st.html(
            '<div class="section-card"><p class="section-subtitle">Brak danych do osi czasu.</p></div>',
        )
        return

    bar_html = "".join(
        f'<div class="timeline-segment {segment["status"]}" '
        f'style="width:{segment["width_percent"]:.2f}%"></div>'
        for segment in segments
    )

    quarter_1 = now + timedelta(minutes=15)
    quarter_2 = now + timedelta(minutes=30)
    quarter_3 = now + timedelta(minutes=45)

    st.html(
        f"""
        <div class="section-card">
            <h3 class="section-title">Timeline</h3>
            <p class="section-subtitle">Przewidywany status rogatek w najbliższej godzinie</p>

            <div class="timeline-wrap">
                <div class="timeline-track">
                    {bar_html}
                </div>

                <div class="timeline-times">
                    <span>{now:%H:%M}</span>
                    <span>{quarter_1:%H:%M}</span>
                    <span>{quarter_2:%H:%M}</span>
                    <span>{quarter_3:%H:%M}</span>
                    <span>{horizon_end:%H:%M}</span>
                </div>

                <div class="timeline-legend">
                    <div class="legend-item">
                        <span class="legend-dot open"></span>
                        <span>otwarte</span>
                    </div>
                    <div class="legend-item">
                        <span class="legend-dot closed"></span>
                        <span>zamknięte</span>
                    </div>
                </div>
            </div>
        </div>
        """,
    )


def render_trains(upcoming_events):
    if not upcoming_events:
        st.html(
            """
            <div class="section-card">
                <h3 class="section-title">Najbliższe przejazdy</h3>
                <p class="section-subtitle">Brak nadchodzących pociągów w zapisanych danych.</p>
            </div>
            """,
        )
        return

    items = []

    for event in upcoming_events:
        train_label = f"{event.get('carrier', '?')} {event.get('category', '?')} {event.get('number', '?')}"
        stop_label = "Zatrzymuje się w Rembertowie" if event.get("type") != "passing" else "Przejazd bez postoju"

        item = f"""
        <div class="train-item">
            <div class="train-time">{event['passage_time']:%H:%M}</div>
            <div class="train-main">
                <div class="train-title">{train_label}</div>
                <div class="train-meta">{event.get('direction', '?')}</div>
                <div class="train-chip-row">
                    <span class="train-chip">{stop_label}</span>
                    <span class="train-chip">{event.get('source', 'dane')}</span>
                </div>
            </div>
        </div>
        """
        items.append(item)

    st.html(
        f"""
        <div class="section-card">
            <h3 class="section-title">Najbliższe przejazdy</h3>
            <p class="section-subtitle">Pomocnicza lista najbliższych pociągów</p>
            <div class="trains-list">
                {''.join(items)}
            </div>
        </div>
        """,
    )


load_styles()
config = load_config()

st.html('<div class="app-shell">')

timestamp = get_data_timestamp()

st.html(
    f"""
    <div class="header-card">
        <div class="header-top">
            <div class="barrier-icon">🚧</div>
            <div>
                <h1 class="app-title">ROGATY REMBERTÓW</h1>
                <p class="app-subtitle">Czy przejdziesz teraz przez tory?</p>
            </div>
        </div>
        <div class="meta-row">
            <div class="meta-pill">Dane aktualizowane automatycznie</div>
            <div class="meta-pill">Ostatnia aktualizacja: {timestamp.strftime("%H:%M") if timestamp else "brak danych"}</div>
        </div>
    </div>
    """,
)

refresh_col_1, refresh_col_2 = st.columns([1, 1])
with refresh_col_1:
    refresh_clicked = st.button("🔄 Odśwież dane", type="primary", use_container_width=True)
with refresh_col_2:
    st.button("📍 Status live", disabled=True, use_container_width=True)

if refresh_clicked:
    try:
        with st.spinner("Odświeżam dane i przeliczam prognozę..."):
            refresh_data()
        st.rerun()
    except Exception as error:
        print(f"Błąd odświeżania: {error}", file=sys.stderr)
        st.error("Nie udało się odświeżyć danych. Spróbuj ponownie za chwilę.")


def data_needs_refresh():
    timestamp = get_data_timestamp()

    if timestamp is None:
        return True

    now_local = datetime.now(WARSAW).replace(tzinfo=None)
    age = now_local - timestamp

    return age >= DATA_MAX_AGE


@st.fragment(run_every=15, parallel=True)
def automatic_data_refresh():
    if not data_needs_refresh():
        return

    try:
        refresh_data()
        st.rerun()
    except Exception as error:
        print(
            f"Automatyczne odświeżanie nie powiodło się: {error}",
            file=sys.stderr,
        )


events = load_events()

if not events:
    try:
        with st.spinner("Pobieram aktualne dane o ruchu pociągów..."):
            refresh_data()
        st.rerun()
    except Exception as error:
        print(f"Błąd pierwszego pobrania danych: {error}", file=sys.stderr)
        st.warning("Aktualne dane są chwilowo niedostępne. Spróbuj ponownie za chwilę.")
        st.stop()

automatic_data_refresh()

closures = build_closures(events, config)
now = datetime.now(WARSAW).replace(tzinfo=None)

future_closures = [
    closure for closure in closures
    if closure["until"] >= now
]

minimum_opening = timedelta(seconds=config["minimum_opening_seconds"])
openings = build_openings(future_closures, minimum_opening)

current_closure = next(
    (closure for closure in future_closures if closure["from"] <= now <= closure["until"]),
    None,
)

next_closure = next(
    (closure for closure in future_closures if closure["from"] > now),
    None,
)

status_class = "unknown"
status_badge = "Brak pełnej pewności"
status_title = "STATUS NIEDOSTĘPNY"
forecast_label = "Sprawdź ponownie za chwilę"
forecast_value = "Brak prognozy"
forecast_secondary = ""

if current_closure:
    status_class = "closed"
    status_badge = "Prawdopodobnie zamknięte"
    status_title = "ROGATKI ZAMKNIĘTE"

    if openings:
        current_opening = next(
            (opening for opening in openings if opening["from"] >= current_closure["until"]),
            None,
        )
        if current_opening:
            forecast_label = "Przewidywane otwarcie"
            forecast_value = f"{current_opening['from']:%H:%M}–{current_opening['until']:%H:%M}"
            forecast_secondary = f"Okno przejścia około {format_duration(current_opening['duration'])}"
        else:
            forecast_label = "Przewidywane otwarcie"
            forecast_value = f"{current_closure['until']:%H:%M}"
    else:
        forecast_label = "Przewidywane otwarcie"
        forecast_value = f"{current_closure['until']:%H:%M}"

else:
    status_class = "open"
    status_badge = "Prawdopodobnie otwarte"
    status_title = "ROGATKI OTWARTE"

    if next_closure:
        remaining = next_closure["from"] - now
        forecast_label = "Przewidywane zamknięcie"
        forecast_value = f"za ok. {format_duration(remaining)}"
        forecast_secondary = f"Około {next_closure['from']:%H:%M}"
    else:
        forecast_label = "Brak kolejnego zamknięcia"
        forecast_value = "Brak w aktualnych danych"

badge_class = "open" if status_class == "open" else "closed" if status_class == "closed" else "warn"
title_class = "open" if status_class == "open" else "closed" if status_class == "closed" else "unknown"

st.html(
    f"""
    <div class="status-card {status_class}">
        <div class="status-accent"></div>
        <div class="status-label">Aktualny status</div>
        <h2 class="status-title {title_class}">{status_title}</h2>
        <div class="status-badge {badge_class}">{status_badge}</div>
        <div class="forecast-label">{forecast_label}</div>
        <div class="forecast-value">{forecast_value}</div>
        {f'<div class="forecast-secondary">{forecast_secondary}</div>' if forecast_secondary else ''}
    </div>
    """,
)

render_timeline(now, future_closures)

if openings:
    opening_cards = "".join(
        f"""
        <div class="opening-item">
            <div class="opening-time">{opening['from']:%H:%M}–{opening['until']:%H:%M}</div>
            <div class="opening-duration">około {format_duration(opening['duration'])}</div>
        </div>
        """
        for opening in openings[:5]
    )

    st.html(
        f"""
        <div class="section-card">
            <h3 class="section-title">Najbliższe okna przejścia</h3>
            <p class="section-subtitle">Przewidywane momenty, kiedy przejazd może być otwarty</p>
            <div class="opening-list">
                {opening_cards}
            </div>
        </div>
        """,
    )

upcoming_events = [
    event for event in events
    if event["passage_time"] >= now - timedelta(minutes=1)
][:10]

save_model_history(status_class, next_closure, timestamp, upcoming_events)

render_trains(upcoming_events)

st.html(
    f"""
    <div class="footer-card">
        <div class="footer-lines">
            <div>Dane aktualizowane automatycznie</div>
            <div>Ostatnia aktualizacja: <span class="footer-strong">{timestamp.strftime("%H:%M") if timestamp else "brak danych"}</span></div>
            <div>Prognoza oparta na ruchu pociągów pasażerskich i modelu przewidywania stanu rogatek.</div>
            <div>Przechodź dopiero po pełnym podniesieniu zapór i wyłączeniu czerwonej sygnalizacji.</div>
        </div>
    </div>
    """,
)

st.html("</div>")
