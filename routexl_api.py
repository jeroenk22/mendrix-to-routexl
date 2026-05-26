"""
routexl_api.py
RouteXL API client.

Tijdvenster-berekening:
  - RouteXL gebruikt minuten *relatief* aan de starttijd van de route
  - ready = max(0, venster_start − route_start)   [vroegste aankomst]
  - due   = venster_eind − route_start             [uiterste aankomst]

Documentatie: https://www.routexl.com/blog/api/
"""

import re
import json
import os
import requests

LOG_PATH = os.path.join(os.path.expanduser("~"), "mendrix_routexl_debug.json")


def _debug_log(locations: list[dict]) -> None:
    text = json.dumps(locations, indent=2, ensure_ascii=False)
    print("\n=== DEBUG: RouteXL verzend-payload ===", flush=True)
    print(text, flush=True)
    print("======================================\n", flush=True)
    try:
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[DEBUG] Payload ook geschreven naar {LOG_PATH}", flush=True)
    except Exception as e:
        print(f"[DEBUG] Kon logbestand niet schrijven: {e}", flush=True)

TOUR_URL = "https://api.routexl.com/tour/"

HTTP_ERRORS = {
    401: "Authenticatie mislukt – controleer gebruikersnaam en wachtwoord.",
    403: "Te veel locaties voor uw RouteXL-abonnement.",
    409: "Geen locaties ontvangen door RouteXL.",
    429: "Er loopt al een andere route. Even wachten en opnieuw proberen.",
    204: "RouteXL kon geen route berekenen.",
}


# ---------------------------------------------------------------------------
# Tijdvenster-hulpfuncties
# ---------------------------------------------------------------------------

def _to_minutes(time_str: str) -> int:
    """'09:00'  →  540"""
    ts = time_str.strip().replace(".", ":")
    h, m = map(int, ts.split(":"))
    return h * 60 + m


def _fmt_arrival(total_minutes: int) -> str:
    """540 → '09:00', 1510 → '01:10 (+1)' (volgende dag)."""
    hh, mm = divmod(total_minutes % (24 * 60), 60)
    suffix = " (+1)" if total_minutes >= 24 * 60 else ""
    return f"{hh:02d}:{mm:02d}{suffix}"


def calculate_restrictions(start_time: str, gewenst: str) -> dict | None:
    """
    Bereken RouteXL ready/due in minuten t.o.v. de starttijd.
    Geeft None terug als er geen tijdvenster is (bijv. Startadres/Eindadres).
    """
    if not gewenst or not start_time:
        return None

    m = re.search(r"(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})", gewenst)
    if not m:
        return None

    start_min = _to_minutes(start_time)
    ready_abs = _to_minutes(m.group(1))
    due_abs   = _to_minutes(m.group(2))
    if due_abs <= ready_abs:          # venster gaat over middernacht (bijv. 17:00 - 04:00)
        due_abs += 24 * 60

    ready = max(0, ready_abs - start_min)
    due   = max(ready + 1, due_abs - start_min)

    return {"ready": ready, "due": due}


# ---------------------------------------------------------------------------
# Route opbouwen
# ---------------------------------------------------------------------------

def build_locations(stops: list[dict], start_time: str) -> list[dict]:
    """
    Zet stop-dicts om naar RouteXL locations-array.
    Verwacht dat elke stop al lat/lon heeft.
    Volgorde: [startadres, ...stops..., eindadres]
    """
    locations = []
    for stop in stops:
        if not stop.get("lat") or not stop.get("lon"):
            continue

        address = (stop.get("matched_address")
                   or f"{stop['straat']}, {stop['postcode']} {stop['plaats']}")

        loc: dict = {
            "address":     address,
            "lat":         str(stop["lat"]),
            "lng":         str(stop["lon"]),
            "servicetime": int(stop.get("service_time", 5)),
        }

        restr = calculate_restrictions(start_time, stop.get("gewenst", ""))
        if restr:
            loc["restrictions"] = restr

        locations.append(loc)

    return locations


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def send_route(username: str, password: str,
               locations: list[dict],
               skip_optimisation: bool = False) -> dict:
    """
    Stuur route naar RouteXL.
    Gooit een Exception bij fouten; geeft het JSON-antwoord terug bij succes.
    """
    payload: dict = {"locations": json.dumps(locations)}
    if skip_optimisation:
        payload["skipOptimisation"] = "true"

    _debug_log(locations)

    r = requests.post(
        TOUR_URL,
        data=payload,
        auth=(username, password),
        timeout=60,
    )

    print(f"[RouteXL] HTTP {r.status_code}: {r.text[:500]}", flush=True)

    if r.status_code != 200:
        msg = HTTP_ERRORS.get(r.status_code,
                               f"HTTP {r.status_code}: {r.text[:200]}")
        raise Exception(msg)

    result = r.json()
    if result is None:
        raise Exception("RouteXL gaf een lege respons terug (null).")
    return result


# ---------------------------------------------------------------------------
# Resultaat formatteren
# ---------------------------------------------------------------------------

def format_result(result: dict, start_time: str) -> str:
    """Geeft een leesbare samenvatting van het RouteXL-antwoord."""
    route    = result.get("route", {})
    count    = result.get("count", 0)
    feasible = result.get("feasible", True)

    start_min = _to_minutes(start_time) if start_time else 0

    lines = [
        f"Route ontvangen van RouteXL",
        f"Stops: {count}",
        f"Uitvoerbaar: {'✓ Ja' if feasible else '✗ Nee – tijdvensters niet haalbaar'}",
        "",
        "Optimale volgorde:",
    ]

    for wp in route.values():
        abs_min = start_min + int(wp["arrival"])
        lines.append(
            f"  • {wp['name']}"
            f"  –  aankomst {_fmt_arrival(abs_min)}"
            f"  ({wp['distance']:.1f} km)"
        )

    return "\n".join(lines)
