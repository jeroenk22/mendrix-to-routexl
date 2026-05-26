"""
tomtom_api.py
TomTom Traffic + Routing API integratie.

Wordt aangeroepen ná RouteXL-optimalisatie om twee zaken toe te voegen:
  1. Verkeersvertraging per stop  (Routing API, traffic=true)
  2. Actieve incidenten op de route (Traffic Incidents API v5)

Documentatie:
  https://developer.tomtom.com/routing-api/documentation/routing/calculate-route
  https://developer.tomtom.com/traffic-api/documentation/traffic-incidents/incident-details
"""

import math
import re
import requests
from datetime import datetime

ROUTING_URL  = "https://api.tomtom.com/routing/1/calculateRoute/{locs}/json"
INCIDENT_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"

MAX_INCIDENT_DIST_KM = 2.0  # incidenten verder dan dit van de route negeren


def _dist_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Rechte-lijn afstand in km tussen twee coördinaten (Pythagoras, voldoende voor <100 km)."""
    dlat = (lat2 - lat1) * 111.0
    dlon = (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


def _point_to_segment_km(
    px: float, py: float,
    x1: float, y1: float,
    x2: float, y2: float,
) -> float:
    """Loodrechte afstand in km van punt (px,py) tot lijnstuk (x1,y1)→(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return _dist_km(px, py, x1, y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return _dist_km(px, py, x1 + t * dx, y1 + t * dy)


def _min_dist_to_route_km(
    lat: float, lon: float,
    waypoints: list[tuple[float, float, str]],
) -> float:
    """Minimale afstand in km van een punt tot de route-lijn (via alle segmenten)."""
    if len(waypoints) < 2:
        return float("inf")
    return min(
        _point_to_segment_km(
            lat, lon,
            waypoints[i][0], waypoints[i][1],
            waypoints[i + 1][0], waypoints[i + 1][1],
        )
        for i in range(len(waypoints) - 1)
    )


def get_route_with_traffic(
    api_key: str,
    waypoints: list[tuple[float, float, str]],
    depart_time: str,
) -> list[dict]:
    """
    Berekent verkeersvertraging per stop via TomTom Routing API.

    waypoints   : [(lat, lon, adres), ...]  in RouteXL-geoptimaliseerde volgorde
    depart_time : "09:00"

    Retourneert één entry per stop (excl. vertrekpunt):
      {"address": str, "arrival_min": int, "delay_sec": int}

    arrival_min : aankomsttijd in minuten na middernacht (lokale tijd)
    delay_sec   : vertraging t.o.v. vrije doorstroming in seconden
    """
    if len(waypoints) < 2:
        return []

    locs = ":".join(f"{lat},{lon}" for lat, lon, _ in waypoints)
    today = datetime.now().strftime("%Y-%m-%d")

    r = requests.get(
        ROUTING_URL.format(locs=locs),
        params={
            "key":        api_key,
            "departAt":   f"{today}T{depart_time}:00",
            "traffic":    "true",
            "travelMode": "van",
        },
        timeout=15,
    )
    r.raise_for_status()

    legs = r.json()["routes"][0]["legs"]
    results = []
    for i, leg in enumerate(legs):
        s = leg["summary"]
        m = re.search(r"T(\d{2}):(\d{2}):", s.get("arrivalTime", ""))
        results.append({
            "address":     waypoints[i + 1][2],
            "arrival_min": int(m.group(1)) * 60 + int(m.group(2)) if m else 0,
            "delay_sec":   s.get("trafficDelayInSeconds", 0) or 0,
        })
    return results


def get_incidents_on_route(
    api_key: str,
    waypoints: list[tuple[float, float, str]],
) -> list[dict]:
    """
    Haalt actieve verkeersincidenten op in het routegebied.

    Filtert incidenten met magnitudeOfDelay < 2 (onbekend/minor) weg.
    Geeft max. 8 incidenten terug als dict:
      {"text": str, "lat": float, "lon": float, "severity": int}
    """
    if not waypoints:
        return []

    lats = [lat for lat, _, _ in waypoints]
    lons = [lon for _, lon, _ in waypoints]
    mg = 0.05  # ~5 km marge rondom de route
    bbox = f"{min(lons)-mg},{min(lats)-mg},{max(lons)+mg},{max(lats)+mg}"

    try:
        r = requests.get(
            INCIDENT_URL,
            params={
                "key":                api_key,
                "bbox":               bbox,
                "fields":             (
                    "{incidents{type,"
                    "geometry{type,coordinates},"
                    "properties{iconCategory,magnitudeOfDelay,"
                    "events{description},from,to,delay,roadNumbers}}}"
                ),
                "language":           "nl-NL",
                "timeValidityFilter": "present",
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    results = []
    for inc in data.get("incidents", []):
        p        = inc.get("properties", {})
        severity = p.get("magnitudeOfDelay") or 0
        if severity < 2:
            continue

        # Eerste coördinaat van geometry gebruiken als locatie op de kaart
        geo   = inc.get("geometry", {})
        coords = geo.get("coordinates", [])
        if geo.get("type") == "LineString" and coords:
            lon_p, lat_p = coords[0][0], coords[0][1]
        elif geo.get("type") == "Point" and coords:
            lon_p, lat_p = coords[0], coords[1]
        else:
            continue  # geen bruikbare geometrie

        events    = p.get("events", [])
        desc      = events[0]["description"] if events else p.get("iconCategory", "incident")
        delay     = round((p.get("delay") or 0) / 60)
        roads     = ", ".join(p.get("roadNumbers") or [])
        parts     = list(filter(None, [
            roads,
            f"van {p['from']}"  if p.get("from") else "",
            f"naar {p['to']}"   if p.get("to")   else "",
        ]))
        loc_str   = " – ".join(parts)
        delay_str = f" (+{delay} min)" if delay > 0 else ""
        text      = f"{desc}{delay_str}" + (f"  –  {loc_str}" if loc_str else "")

        if _min_dist_to_route_km(lat_p, lon_p, waypoints) <= MAX_INCIDENT_DIST_KM:
            results.append({"text": text, "lat": lat_p, "lon": lon_p, "severity": severity})

    return results[:8]
