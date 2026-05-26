"""
geocoder.py
Geocodeert adressen naar lat/lon.
- PDOK Locatieserver  → Nederlandse adressen (gratis, snel)
- Geopunt             → Belgische adressen (gratis, Vlaamse overheid, geen sleutel)
- Nominatim (OSM)     → Overige buitenlandse adressen (gratis, 1 req/sec)

Fallback-strategie:
  1. NL postcode → PDOK met volledig adres
  2. PDOK mislukt → PDOK met alleen straat+plaats
  3. 4-cijferige postcode (BE) → Geopunt, daarna Nominatim BE
  4. 5-cijferige postcode (DE) → Nominatim DE
"""

import re
import time
import requests

PDOK_URL      = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
GEOPUNT_URL   = "https://loc.geopunt.be/v4/Location"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

RE_NL_POSTCODE = re.compile(r"^\d{4}\s?[A-Z]{2}$", re.IGNORECASE)
RE_BE_POSTCODE = re.compile(r"^\d{4}$")
RE_DE_POSTCODE = re.compile(r"^\d{5}$")
# Postcode-letters die in de plaatsnaam zijn beland door OCR-fout
RE_PC_PREFIX   = re.compile(r"^([A-Z]{1,2})\s+(.+)$", re.IGNORECASE)


def is_dutch(postcode: str) -> bool:
    return bool(RE_NL_POSTCODE.match(postcode.strip()))


def _could_be_dutch(postcode: str) -> bool:
    """True voor volledige én gedeeltelijke NL-postcodes (4 cijfers + 0-2 letters).
    Puur 4-cijferig (geen letters) is AMBIGU: kan NL (letters gemist door OCR)
    of Belgisch zijn — wordt apart afgehandeld via _city_matches.
    """
    return bool(re.match(r"^\d{4}", postcode.strip()))


def _is_pure_4digit(postcode: str) -> bool:
    return bool(re.match(r"^\d{4}$", postcode.strip()))


def _city_matches(pdok_addr: str, plaats: str) -> bool:
    """Controleert of de door PDOK gevonden plaatsnaam overeenkomt met de verwachte."""
    if not pdok_addr or not plaats:
        return True
    return plaats.strip().lower() in pdok_addr.lower()


def _country_hint(postcode: str) -> str | None:
    """Geeft ISO-landcode terug op basis van postcode-formaat."""
    p = postcode.strip()
    if RE_BE_POSTCODE.match(p):
        return "be"
    if RE_DE_POSTCODE.match(p):
        return "de"
    return None


def _try_fix_partial_postcode(postcode: str, plaats: str) -> tuple[str, str]:
    """
    Als postcode 4-cijferig is én plaats begint met 1-2 hoofdletters,
    zijn de postcode-letters waarschijnlijk door OCR als plaatsnaam gelezen.
    Geeft (gecorrigeerde_postcode, gecorrigeerde_plaats) terug.
    """
    if not RE_BE_POSTCODE.match(postcode.strip()):
        return postcode, plaats
    m = RE_PC_PREFIX.match(plaats.strip())
    if m:
        full_pc = postcode.strip() + " " + m.group(1).upper()
        if is_dutch(full_pc.replace(" ", "")):
            return full_pc, m.group(2).strip()
    return postcode, plaats


def geocode(
    straat: str, postcode: str, plaats: str
) -> tuple[float | None, float | None, str | None, str | None, str | None]:
    """
    Geocodeer een adres.
    Geeft (lat, lon, gevonden_adres, gebruikte_postcode, gebruikte_plaats) terug.
    Alle waarden None bij mislukking.
    """
    # Herstel OCR-fout: postcode-letters beland in plaatsnaam ("BJ Best" → pc="5681 BJ", pl="Best")
    pc_fixed, pl_fixed = _try_fix_partial_postcode(postcode, plaats)
    if pc_fixed != postcode:
        print(f"[Geocoder] Postcode gecorrigeerd: '{postcode} {plaats}' → '{pc_fixed} {pl_fixed}'", flush=True)

    if is_dutch(pc_fixed):
        # Volledige NL postcode → PDOK
        lat, lon, addr = _pdok(straat, pc_fixed, pl_fixed)
        if lat is not None:
            return lat, lon, addr, pc_fixed, pl_fixed
        # Retry zonder postcode (OCR-fout in letters)
        print(f"[PDOK] Retry zonder postcode: '{straat}', '{pl_fixed}'", flush=True)
        lat, lon, addr = _pdok(straat, "", pl_fixed)
        if lat is not None:
            return lat, lon, addr, pc_fixed, pl_fixed

    elif re.match(r"^\d{4}\s?[A-Z]{1}$", pc_fixed.strip(), re.IGNORECASE):
        # Partieel NL (1 letter door OCR) → PDOK zonder postcode
        print(f"[PDOK] Partieel NL postcode, retry zonder postcode: '{straat}', '{pl_fixed}'", flush=True)
        lat, lon, addr = _pdok(straat, "", pl_fixed)
        if lat is not None:
            return lat, lon, addr, pc_fixed, pl_fixed

    elif RE_BE_POSTCODE.match(pc_fixed.strip()):
        # 4-cijferig (BE) → eerst Geopunt (Vlaamse overheid), dan Nominatim, dan PDOK
        lat, lon, addr = _geopunt(straat, pc_fixed, pl_fixed)
        if lat is not None:
            return lat, lon, addr, pc_fixed, pl_fixed
        lat, lon, addr = _nominatim(straat, pc_fixed, pl_fixed, "be")
        if lat is not None:
            return lat, lon, addr, pc_fixed, pl_fixed
        lat, lon, addr = _pdok(straat, "", pl_fixed)
        if lat is not None and _city_matches(addr, pl_fixed):
            return lat, lon, addr, pc_fixed, pl_fixed

    else:
        # 5-cijferig (DE) of overig → Nominatim met landcode
        country = _country_hint(pc_fixed)
        lat, lon, addr = _nominatim(straat, pc_fixed, pl_fixed, country)
        if lat is not None:
            return lat, lon, addr, pc_fixed, pl_fixed

        # Fallback 1: postcode-letters beland in plaatsnaam (bijv. "PE Epe")
        m_prefix = RE_PC_PREFIX.match(pl_fixed.strip())
        if m_prefix:
            pl_clean = m_prefix.group(2).strip()
            print(f"[Geocoder] 5-cijfer+prefix mislukt, retry PDOK: '{straat}', '{pl_clean}'", flush=True)
            lat, lon, addr = _pdok(straat, "", pl_clean)
            if lat is not None:
                return lat, lon, addr, pc_fixed, pl_clean

        # Fallback 2: 5-cijferige postcode is vermoedelijk een OCR-fout op een NL postcode
        # (bijv. "8102 SK" → "83102"). Probeer PDOK met alleen straat + plaatsnaam.
        print(f"[Geocoder] 5-cijfer mislukt, retry PDOK straat+plaats: '{straat}', '{pl_fixed}'", flush=True)
        lat, lon, addr = _pdok(straat, "", pl_fixed)
        if lat is not None:
            return lat, lon, addr, pc_fixed, pl_fixed

    return None, None, None, postcode, plaats


def _pdok(straat: str, postcode: str, plaats: str) -> tuple:
    """PDOK Locatieserver – alleen NL adressen."""
    q = " ".join(filter(None, [straat, postcode, plaats]))
    try:
        r = requests.get(
            PDOK_URL,
            params={"q": q, "rows": 1, "fl": "centroide_ll,weergavenaam"},
            timeout=8,
        )
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
        if docs:
            m = re.match(r"POINT\(([^\s]+)\s+([^\s]+)\)",
                         docs[0].get("centroide_ll", ""))
            if m:
                addr = docs[0].get("weergavenaam", "")
                return float(m.group(2)), float(m.group(1)), addr
    except Exception as e:
        print(f"[PDOK] {e}", flush=True)
    return None, None, None


_geopunt_ok = True   # circuit breaker: False na eerste verbindingsfout


def _geopunt(straat: str, postcode: str, plaats: str) -> tuple:
    """Geopunt Locatieservice – Belgische/Vlaamse adressen (gratis, geen API-sleutel)."""
    global _geopunt_ok
    if not _geopunt_ok:
        return None, None, None
    q = " ".join(filter(None, [straat, postcode, plaats]))
    print(f"[Geopunt] {q}", flush=True)
    try:
        r = requests.get(
            GEOPUNT_URL,
            params={"q": q, "c": 1, "language": "nl"},
            timeout=8,
        )
        r.raise_for_status()
        results = r.json().get("LocationResult", [])
        if results:
            loc  = results[0]["Location"]
            addr = results[0].get("FormattedAddress", "")
            return float(loc["Lat_WGS84"]), float(loc["Lon_WGS84"]), addr
    except requests.exceptions.ConnectionError:
        _geopunt_ok = False
        print("[Geopunt] Niet bereikbaar – overgeslagen voor deze sessie.", flush=True)
    except Exception as e:
        print(f"[Geopunt] {e}", flush=True)
    return None, None, None


def _nominatim(straat: str, postcode: str, plaats: str,
               country: str | None = None) -> tuple:
    """Nominatim (OpenStreetMap) – gestructureerde search, rate-limit 1 req/sec.
    Gestructureerd voorkomt dat bedrijfsnamen of POI's als resultaat komen.
    """
    time.sleep(1.1)
    # Gestructureerde parameters: straat, postcode en stad apart meegeven
    params: dict = {
        "street":     straat,
        "postalcode": postcode,
        "city":       plaats,
        "format":     "json",
        "limit":      1,
        "addressdetails": 0,
    }
    # Verwijder lege velden
    params = {k: v for k, v in params.items() if v}
    if country:
        params["countrycodes"] = country
    print(f"[Nominatim] land={country or '?'}: {straat}, {postcode} {plaats}", flush=True)
    try:
        r = requests.get(
            NOMINATIM_URL,
            params=params,
            headers={"User-Agent": "MendrixRouteXL/1.0"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json()
        if results:
            addr = results[0].get("display_name", "")
            return float(results[0]["lat"]), float(results[0]["lon"]), addr
        # Fallback: vrije tekst als gestructureerd niets oplevert
        q = ", ".join(filter(None, [straat, f"{postcode} {plaats}".strip()]))
        fb_params: dict = {"q": q, "format": "json", "limit": 1}
        if country:
            fb_params["countrycodes"] = country
        r2 = requests.get(
            NOMINATIM_URL, params=fb_params,
            headers={"User-Agent": "MendrixRouteXL/1.0"}, timeout=10,
        )
        r2.raise_for_status()
        results2 = r2.json()
        if results2:
            addr = results2[0].get("display_name", "")
            return float(results2[0]["lat"]), float(results2[0]["lon"]), addr
    except Exception as e:
        print(f"[Nominatim] {e}", flush=True)
    return None, None, None
