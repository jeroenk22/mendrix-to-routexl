"""
ocr_parser.py
Verwerkt een screenshot van het Mendrix ritten-scherm naar gestructureerde stop-data.

Strategie: gebruik postcode als anker.
- Dutch postcode: 4 cijfers + 2 letters  (7122 JD)
- German postcode: 5 cijfers             (49586)

Kolommen in de tabel (van links naar rechts):
  ... | Naam/Label | Straat | Postcode | Plaats | Gewenst | ...
"""

import re
import cv2
import numpy as np
import pytesseract
from pytesseract import Output
from PIL import Image

# --- Regex patterns ---
RE_POSTCODE_NL  = re.compile(r"^\d{4}\s?[A-Z]{2}$", re.IGNORECASE)
RE_POSTCODE_DE  = re.compile(r"^\d{5}$")
RE_HOUSENUMBER  = re.compile(r"^\d+[a-zA-Z\-]?$")
RE_TIME         = re.compile(r"^\d{1,2}[:.]\d{2}$")
RE_TIME_RANGE   = re.compile(r"(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})")

Y_TOLERANCE = 20  # pixels in 4x-geschaald beeld – ruimer dan 3x om label-iconen te absorberen
MIN_CONFIDENCE = 15             # verlaagd van 20→15: bij 4× schaal lager vertrouwen per token
MIN_CONFIDENCE_PC_LETTERS = 10  # lager voor potentiële postcode-letters (1-2 hoofdletters)
MIN_CONFIDENCE_TIME = 12        # lager voor tijdtokens (HH:MM of losse cijfers in tijdvenster)


# ---------------------------------------------------------------------------
# Publieke interface
# ---------------------------------------------------------------------------

def parse_screenshot(img: Image.Image) -> list[dict]:
    """
    Parseer een screenshot van de Mendrix-tabel.
    Geeft een lijst van stops terug; elk stop-dict heeft:
        naam, straat, postcode, plaats, gewenst, row_type ('start'|'stop'|'end')
    """
    processed = _preprocess(img)

    # Debug: sla preprocessed beeld op zodat je kunt zien wat Tesseract als input krijgt
    import os, tempfile
    from PIL import Image as _PILImage
    dbg_path = os.path.join(tempfile.gettempdir(), "mendrix_ocr_debug.png")
    _PILImage.fromarray(processed).save(dbg_path)
    print(f"[OCR debug] Preprocessed beeld: {dbg_path}", flush=True)

    data = pytesseract.image_to_data(
        processed,
        output_type=Output.DICT,
        lang="nld+deu",
        config="--psm 6 --oem 3 --dpi 400"
    )

    # Debug: print alle woorden met hun confidence zodat lage-confidence tokens zichtbaar zijn
    print("[OCR debug] Alle tokens (conf ≥ 0):", flush=True)
    for i in range(len(data["text"])):
        t = data["text"][i].strip()
        if t:
            print(f"  conf={data['conf'][i]:3d}  '{t}'", flush=True)

    rows  = _cluster_rows(data)
    strip_morgen = _detect_morgen_route(rows)
    if strip_morgen:
        print("[OCR] Volgende-dag route gedetecteerd – '(morgen)' wordt gefilterd.", flush=True)
    stops = [_parse_row(r, strip_morgen=strip_morgen) for r in rows]
    stops = [s for s in stops if s is not None]
    _infer_start_end(stops)
    return stops


def _has_morgen(row: list[dict]) -> bool:
    return any(re.search(r"\bmorgen\b", w["text"], re.IGNORECASE) for w in row)


def _detect_morgen_route(rows: list[list[dict]]) -> bool:
    """True als startadres + minstens 2 van de eerste 3 stops '(morgen)' bevatten."""
    check = rows[:4]
    return sum(1 for r in check if _has_morgen(r)) >= 3


def _infer_start_end(stops: list[dict]) -> None:
    """
    Fallback als 'Startadres'/'Eindadres' niet in de tekst staat
    (bijv. als de Naam-kolom buiten de selectie viel).
    Alleen de EERSTE rij kan startadres worden en alleen de LAATSTE kan
    eindadres worden — Mendrix plaatst ze altijd op die posities in de tabel.
    Tussenliggende rijen met een enkelvoudig tijdstip (OCR-fout op het
    streepje in '10:00 - 14:00') worden zo nooit verkeerd geclassificeerd.
    """
    if any(s["row_type"] == "start" for s in stops):
        return  # Tekst-detectie heeft al gewerkt

    if stops and _is_single_time(stops[0].get("gewenst", "")):
        stops[0]["row_type"] = "start"

    if len(stops) > 1 and _is_single_time(stops[-1].get("gewenst", "")):
        stops[-1]["row_type"] = "end"


def validate_stops(stops: list[dict]) -> tuple[bool, str]:
    """Controleer of de vereiste kolommen aanwezig zijn."""
    if not stops:
        return False, "Geen adressen herkend in de selectie."

    errors = []
    for i, s in enumerate(stops):
        if s["row_type"] in ("start", "end"):
            continue
        # Postcode is optioneel als straat + plaats aanwezig zijn
        missing = [k for k in ("straat", "plaats") if not s.get(k, "").strip()]
        if missing:
            errors.append(f"Rij {i+1}: {', '.join(missing)} ontbreekt")

    if errors:
        return False, (
            "Niet alle vereiste kolommen konden worden uitgelezen:\n"
            + "\n".join(errors[:5])
            + ("\n..." if len(errors) > 5 else "")
        )
    return True, ""


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------

def _neutralize_row_colors(arr: np.ndarray) -> np.ndarray:
    """
    Vervang gekleurde rijachtergronden (oranje, rood, groen, geel) door wit
    zodat Otsu-threshold de tekst op die rijen even goed pakt als op witte rijen.
    Donkere pixels (tekst) en witte/grijze achtergronden worden ongemoeid gelaten.
    """
    hsv  = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    # Gekleurde achtergrond: saturatie hoog genoeg + helderheid hoog genoeg
    # (zwarte tekst heeft V laag; witte achtergrond heeft S laag)
    mask = cv2.inRange(hsv,
                       np.array([0,  40, 150], dtype=np.uint8),
                       np.array([180, 255, 255], dtype=np.uint8))
    result = arr.copy()
    result[mask > 0] = [255, 255, 255]
    return result


def _preprocess(img: Image.Image) -> np.ndarray:
    """Vergroot, ontkleurt en verwijdert tabellijnen voor betere OCR."""
    w, h = img.size
    # 4× i.p.v. 3× zodat verkleinde Mendrix-weergave (meer rijen zichtbaar)
    # nog voldoende resolutie geeft voor Tesseract (~384 DPI bij 96 DPI-scherm).
    img = img.resize((w * 4, h * 4), Image.LANCZOS)
    arr = np.array(img)

    # Wit kader rondom het beeld: geeft Tesseract baseline-context voor de
    # bovenste en onderste rij, die anders aan de rand worden afgesneden.
    PAD = 24
    arr = cv2.copyMakeBorder(arr, PAD, PAD, PAD, PAD,
                              cv2.BORDER_CONSTANT, value=[255, 255, 255])

    # Neutraliseer gekleurde rijachtergronden (oranje/rood/groen in Mendrix)
    arr  = _neutralize_row_colors(arr)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    # Verwijder horizontale tabellijnen (kernel proportioneel aan 4× schaal)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (120, 1))
    h_lines  = cv2.morphologyEx(gray, cv2.MORPH_OPEN, h_kernel, iterations=2)
    gray     = cv2.add(gray, h_lines)

    # CLAHE – lokaal contrast verbeteren
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray  = clahe.apply(gray)

    # Unsharp mask – randen verscherpen
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.5)
    gray    = cv2.addWeighted(gray, 1.8, blurred, -0.8, 0)

    # Binariseer
    _, binary = cv2.threshold(gray, 0, 255,
                               cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


# ---------------------------------------------------------------------------
# Rij-clustering
# ---------------------------------------------------------------------------

RE_PC_LETTERS  = re.compile(r"^[A-Z]{1,2}$", re.IGNORECASE)
RE_PC_DIGITS   = re.compile(r"^\d{4}$")
RE_TIME_TOKEN  = re.compile(r"^\d{1,2}[:.]\d{2}$")  # tijdtoken zoals "07:00"
RE_DASH_TOKEN  = re.compile(r"^[-–]$")               # tijdvenster-streepje


def _cluster_rows(data: dict) -> list[list[dict]]:
    """Groepeer OCR-woorden op Y-coördinaat → lijst van rijen."""
    buckets: dict[int, list[dict]] = {}
    n = len(data["text"])

    for i in range(n):
        conf = int(data["conf"][i])
        text = data["text"][i].strip()
        if not text:
            continue

        # Laat lage-confidence woorden door als ze een herkenbare vorm hebben.
        if conf < MIN_CONFIDENCE:
            top  = data["top"][i]
            left = data["left"][i]

            # Postcode-letters (bijv. "JD") alleen na een 4-cijferig getal
            if conf >= MIN_CONFIDENCE_PC_LETTERS and RE_PC_LETTERS.match(text):
                bucket_key = next((y for y in buckets if abs(y - top) <= Y_TOLERANCE), None)
                if bucket_key is None:
                    continue
                row_words = buckets[bucket_key]
                if not row_words or not RE_PC_DIGITS.match(row_words[-1]["text"]):
                    continue
            # Postcode-cijfers (bijv. "7122") – bij 4× schaal kan de confidence laag zijn;
            # doorlaten zodat _find_postcode het anker kan vinden.
            elif conf >= MIN_CONFIDENCE_PC_LETTERS and RE_PC_DIGITS.match(text):
                pass  # altijd opnemen als potentieel postcode-anker
            # Huisnummer (bijv. "4N", "73") in een rij die al woorden heeft.
            elif conf >= MIN_CONFIDENCE_PC_LETTERS and RE_HOUSENUMBER.match(text):
                bucket_key = next((y for y in buckets if abs(y - top) <= Y_TOLERANCE), None)
                if bucket_key is None:
                    continue
            # Tijdtoken ("07:00") of tijdvenster-streepje ("-") in een rij die al woorden heeft
            elif conf >= MIN_CONFIDENCE_TIME and (
                RE_TIME_TOKEN.match(text) or RE_DASH_TOKEN.match(text)
            ):
                bucket_key = next((y for y in buckets if abs(y - top) <= Y_TOLERANCE), None)
                if bucket_key is None:
                    continue  # Geen bijbehorende rij → negeer
            else:
                continue
        else:
            top  = data["top"][i]
            left = data["left"][i]

        matched = next(
            (y for y in buckets if abs(y - top) <= Y_TOLERANCE),
            None
        )
        key = matched if matched is not None else top
        buckets.setdefault(key, []).append({"text": text, "x": left})

    return [
        sorted(buckets[y], key=lambda w: w["x"])
        for y in sorted(buckets)
    ]


# ---------------------------------------------------------------------------
# Rij-parsing
# ---------------------------------------------------------------------------

def _normalize_text(t: str) -> str:
    """Verwijder veelvoorkomende OCR-fouten."""
    return t.replace("O", "0").replace("o", "0").strip()


def _normalize_german(t: str) -> str:
    """Vervang Duitse Ringel-S (ß) door 'ss'."""
    return t.replace("ß", "ss").replace("ẞ", "SS")


def _clean_field(s: str) -> str:
    """Verwijder OCR-ruis aan begin en eind van een veldwaarde.

    Vangt tabelranden en iconen op die als leestekens of losse letters
    worden gelezen, bijv. '\\', '|', ',', ':', '_', 'i '.
    """
    # Strip niet-alfanumerieke tekens aan begin en eind
    s = re.sub(r'^[^a-zA-Z0-9]+', '', s)
    s = re.sub(r'[^a-zA-Z0-9./-]+$', '', s)
    # Strip enkel losstaand lowercase-letter aan het begin (bijv. "i Keplerlaan")
    s = re.sub(r'^[a-z]\s+', '', s)
    return s.strip()


def _find_postcode(words: list[dict]) -> tuple[int | None, str | None, int]:
    """
    Zoek postcode in een rij.
    Geeft (index, waarde, span) terug; span=1 of 2 woorden.
    """
    for i, w in enumerate(words):
        t = w["text"].upper()
        tn = _normalize_text(t)

        # Één woord: "7122JD" of "49586"
        if RE_POSTCODE_NL.match(tn.replace(" ", "")):
            return i, tn, 1
        if RE_POSTCODE_DE.match(tn) and tn.isdigit() and len(tn) == 5:
            return i, tn, 1

        # Twee woorden: "7122" + "JD"
        # Controleer ook words[i+2] voor het geval er een rommelwoord tussenin zit.
        # Strip niet-letters uit `b` zodat "BJ." of "BJ," ook matcht.
        if re.match(r"^\d{4}$", tn):
            for lookahead in range(1, 3):  # probeer +1 en +2
                if i + lookahead >= len(words):
                    break
                b_raw = words[i + lookahead]["text"].upper().strip()
                b = re.sub(r"[^A-Z]", "", b_raw)  # houd alleen letters
                if re.match(r"^[A-Z]{1,2}$", b):
                    return i, tn + " " + b, lookahead + 1
                # Als het geen postcode-letters zijn, stop dan zoeken
                if b and not re.match(r"^[A-Z]{1,2}$", b):
                    break

        # Belgisch: "B-3550" of "B3550" als één woord
        stripped = re.sub(r"^B[-–]?", "", tn)
        if re.match(r"^\d{4}$", stripped) and 1000 <= int(stripped) <= 9999:
            return i, stripped, 1

        # Belgisch / niet-herkend NL: losse 4-cijferige postcode (1000–9999)
        # Alleen gebruiken als er géén letterwoord direct na staat (anders is het NL)
        if re.match(r"^\d{4}$", tn) and 1000 <= int(tn) <= 9999:
            return i, tn, 1

    return None, None, 1


def _fix_time(t: str) -> str:
    t = t.strip().replace(".", ":")
    parts = t.split(":")
    if len(parts) != 2:
        return t
    try:
        h_str, m_str = parts
        h = int(h_str)
        m = int(m_str)
        if h > 23 and len(h_str) >= 2:
            fixed = "0" + h_str[1:]
            fh = int(fixed)
            if 0 <= fh <= 23 and 0 <= m <= 59:
                return f"{fh:02d}:{m:02d}"
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except ValueError:
        pass
    return t


def _extract_gewenst(words_after: list[dict]) -> tuple[str, int]:
    """
    Haal tijdvenster uit de woorden ná de postcode.
    Geeft (gewenst_string, aantal_woorden) terug.
    """
    text = " ".join(w["text"] for w in words_after)
    m = RE_TIME_RANGE.search(text)
    if m:
        t1 = _fix_time(m.group(1))
        t2 = _fix_time(m.group(2))
        gewenst = f"{t1} - {t2}"
        # Schat het aantal woorden: typisch 3 (HH:MM - HH:MM)
        return gewenst, min(3, len(words_after))

    # Twee opeenvolgende tijden zonder streepje (dash weggefilterd door OCR)
    if len(words_after) >= 2:
        t1 = words_after[-2]["text"]
        t2 = words_after[-1]["text"]
        if RE_TIME.match(t1) and RE_TIME.match(t2):
            return f"{_fix_time(t1)} - {_fix_time(t2)}", 2

    # Enkel tijdstip (Startadres / Eindadres)
    if words_after and RE_TIME.match(words_after[-1]["text"]):
        return _fix_time(words_after[-1]["text"]), 1

    return "", 0


def _split_naam_straat(words_before: list[dict], row_type: str) -> tuple[str, str]:
    """
    Splits woorden vóór de postcode in (straat, naam).
    Straat eindigt op een huisnummer (getal + optioneel letter).
    """
    if row_type == "start":
        return "", "Startadres"
    if row_type == "end":
        return "", "Eindadres"

    # Zoek huisnummer van rechts naar links
    hn_idx = next(
        (i for i in range(len(words_before) - 1, -1, -1)
         if RE_HOUSENUMBER.match(words_before[i]["text"])),
        None
    )

    if hn_idx is not None:
        street_start = max(0, hn_idx - 4)
        street_words = words_before[street_start: hn_idx + 1]
        naam_words   = words_before[:street_start]
    else:
        # Geen huisnummer – neem laatste 3 woorden als straat
        take = min(3, len(words_before))
        street_words = words_before[-take:]
        naam_words   = words_before[:-take] if take else []

    straat = " ".join(w["text"] for w in street_words).strip()
    naam   = " ".join(w["text"] for w in naam_words).strip()
    return straat, naam


def _is_single_time(t: str) -> bool:
    return bool(re.match(r"^\d{1,2}:\d{2}$", t.strip()))


def _detect_row_type(words: list[dict]) -> str:
    full = " ".join(w["text"] for w in words).lower()
    if "startadres" in full:
        return "start"
    if "eindadres" in full:
        return "end"
    return "stop"


def _parse_row_no_postcode(words: list[dict]) -> dict | None:
    """
    Parseer een rij zonder herkenbare postcode (bijv. 'europaweg 25 Arnhem 08:00 - 17:00').
    Heuristiek: tijdvenster aan het einde, laatste niet-getal woord = plaatsnaam.
    """
    if len(words) < 2:
        return None

    row_type = _detect_row_type(words)
    gewenst, gewenst_span = _extract_gewenst(words)
    remaining = words[:len(words) - gewenst_span] if gewenst_span else list(words)

    if len(remaining) < 2:
        return None

    # Laatste woord dat geen huisnummer of tijdtoken is = plaatsnaam
    plaats_idx = len(remaining) - 1
    while plaats_idx > 0 and (
        RE_HOUSENUMBER.match(remaining[plaats_idx]["text"]) or
        RE_TIME.match(remaining[plaats_idx]["text"])
    ):
        plaats_idx -= 1

    if plaats_idx < 1:
        return None

    straat = _normalize_german(_clean_field(" ".join(w["text"] for w in remaining[:plaats_idx])))
    plaats = _normalize_german(_clean_field(remaining[plaats_idx]["text"]))

    if not straat or not plaats:
        return None

    print(f"[Row-noPc] straat='{straat}' plaats='{plaats}' gewenst='{gewenst}'", flush=True)
    return {
        "naam":     "",
        "straat":   straat,
        "postcode": "",
        "plaats":   plaats,
        "gewenst":  gewenst,
        "row_type": row_type,
        "lat":      None,
        "lon":      None,
    }


def _parse_row(words: list[dict], strip_morgen: bool = False) -> dict | None:
    """Parseer één rij naar een stop-dict."""
    if strip_morgen:
        words = [w for w in words if not re.search(r"[\(\[]?morgen[\)\]]?", w["text"], re.IGNORECASE)]
    tokens = [w["text"] for w in words]
    pc_idx, pc_val, pc_span = _find_postcode(words)
    print(f"[Row] {tokens}  →  postcode={pc_val}", flush=True)
    if pc_idx is None:
        return _parse_row_no_postcode(words)  # words zijn al gefilterd als strip_morgen

    row_type = _detect_row_type(words)

    words_before = words[:pc_idx]
    words_after  = words[pc_idx + pc_span:]

    gewenst, gewenst_span = _extract_gewenst(words_after)
    place_words           = words_after[:len(words_after) - gewenst_span]
    plaats                = _clean_field(" ".join(w["text"] for w in place_words))

    straat, naam = _split_naam_straat(words_before, row_type)
    straat = _normalize_german(_clean_field(straat))
    naam   = _normalize_german(_clean_field(naam))
    plaats = _normalize_german(plaats)

    return {
        "naam":     naam,
        "straat":   straat,
        "postcode": pc_val,
        "plaats":   plaats,
        "gewenst":  gewenst,
        "row_type": row_type,   # 'start' | 'stop' | 'end'
        "lat":      None,
        "lon":      None,
    }
