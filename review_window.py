"""
review_window.py
Controleer- en bewerkscherm dat na de OCR-stap wordt getoond.

Toont:
  • Startadres + starttijd (aanpasbaar, of handmatig invoeren als ontbreekt)
  • Lijst van stops met checkbox, handelingstijd, tijdvenster, geocode-status
  • Eindadres (aanpasbaar)
  • RouteXL-inlogvelden
  • Knop 'Doorzetten naar RouteXL'
"""

import json
import os
import re
import tempfile
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox, simpledialog

from config import load_credentials, save_credentials, load_tomtom_key, save_tomtom_key
from geocoder import geocode
from routexl_api import build_locations, send_route, format_result, calculate_restrictions, _fmt_arrival
from tomtom_api import get_route_with_traffic, get_incidents_on_route

PAD = 12
BG  = "#F5F5F5"


class ReviewWindow:
    def __init__(self, parent: tk.Tk, stops: list[dict]):
        self.parent = parent
        self.stops  = stops

        self.start_time_var    = tk.StringVar(value="")
        self.username_var      = tk.StringVar()
        self.password_var      = tk.StringVar()
        self.tomtom_key_var    = tk.StringVar(value=load_tomtom_key())
        self.status_var        = tk.StringVar(value="Adressen geocoderen…")
        self._tomtom_incidents: list[dict] = []

        _user, _pw = load_credentials()
        self.username_var.set(_user)
        self.password_var.set(_pw)

        self._detect_start_end()
        self._dedup_stops()
        self._debug_print_stops()
        self._build_window()
        self._start_geocoding()

    # ------------------------------------------------------------------
    # Debug: toon OCR-resultaat direct na selectie
    # ------------------------------------------------------------------

    def _debug_print_stops(self):
        safe = [
            {k: v for k, v in s.items() if not k.startswith("_")}
            for s in self.stops
        ]
        print("\n=== DEBUG: OCR-resultaat (stops) ===", flush=True)
        print(json.dumps(safe, indent=2, ensure_ascii=False), flush=True)
        print("=====================================\n", flush=True)

    # ------------------------------------------------------------------
    # Dedupliceer stops op adres
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_addr(s: str) -> str:
        """Verwijder OCR-ruis (underscores, punten, extra spaties) voor vergelijking."""
        return re.sub(r"[^a-z0-9]", "", s.lower())

    def _dedup_stops(self):
        seen: set = set()
        seen_addr: set = set()  # straat+plaats zonder postcode (vangt OCR-fouten op)
        deduped: list = []
        for stop in self.stops:
            key = (
                self._norm_addr(stop.get("straat",   "")),
                self._norm_addr(stop.get("postcode", "")),
                self._norm_addr(stop.get("plaats",   "")),
                stop.get("row_type", "stop"),
            )
            straat_n = self._norm_addr(stop.get("straat", ""))
            plaats_n = self._norm_addr(stop.get("plaats", ""))
            # Alleen matchen op straat+plaats als beide velden gevuld zijn
            key_addr = (straat_n, plaats_n, stop.get("row_type", "stop")) if (straat_n and plaats_n) else None

            if key in seen or (key_addr and key_addr in seen_addr):
                continue
            seen.add(key)
            if key_addr:
                seen_addr.add(key_addr)
            deduped.append(stop)

        removed = len(self.stops) - len(deduped)
        if removed:
            print(f"[Dedup] {removed} dubbele stop(s) verwijderd.", flush=True)
        self.stops = deduped

    # ------------------------------------------------------------------
    # Detecteer start/eind uit OCR-resultaat
    # ------------------------------------------------------------------

    def _detect_start_end(self):
        for s in self.stops:
            if s["row_type"] == "start":
                m = re.search(r"\d{1,2}:\d{2}", s.get("gewenst", ""))
                if m:
                    self.start_time_var.set(m.group(0))
                break

        self.has_start = any(s["row_type"] == "start" for s in self.stops)
        self.has_end   = any(s["row_type"] == "end"   for s in self.stops)

        # Geen startadres in selectie → zoek de eerste stop met een ENKEL tijdstip (geen bereik).
        # Stops met een tijdvenster (bijv. "06:00 - 20:00") zijn nooit het startadres.
        if not self.has_start:
            first = next(
                (s for s in self.stops
                 if s["row_type"] == "stop"
                 and re.match(r"^\d{1,2}[:.]\d{2}$", s.get("gewenst", "").strip())),
                None,
            )
            if first:
                first["row_type"] = "start"
                first["naam"]     = "Startadres"
                self.start_time_var.set(re.sub(r"\.", ":", first["gewenst"].strip()))
                self.has_start = True
            else:
                # Fallback: neem de eerste stop als startadres.
                # Als die een tijdvenster heeft, gebruik dan de begintijd als starttijd.
                first = next((s for s in self.stops if s["row_type"] == "stop"), None)
                if first:
                    first["row_type"] = "start"
                    first["naam"]     = "Startadres"
                    m_range = re.search(
                        r"(\d{1,2}[:.]\d{2})\s*[-–]\s*\d{1,2}[:.]\d{2}",
                        first.get("gewenst", ""),
                    )
                    if m_range:
                        self.start_time_var.set(re.sub(r"\.", ":", m_range.group(1)))
                    self.has_start = True

        # Geen eindadres in selectie → promoveer de laatste stop tot eindadres
        if not self.has_end:
            last = next((s for s in reversed(self.stops) if s["row_type"] == "stop"), None)
            if last:
                last["row_type"] = "end"
                last["naam"]     = "Eindadres"
                self.has_end = True

    # ------------------------------------------------------------------
    # Venster opbouwen
    # ------------------------------------------------------------------

    def _build_window(self):
        self.win = tk.Toplevel(self.parent)
        self.win.title("Route controleren – Mendrix → RouteXL")
        self.win.geometry("820x680")
        self.win.configure(bg=BG)
        self.win.resizable(True, True)

        # Hoofd-frame met padding
        outer = tk.Frame(self.win, bg=BG, padx=PAD, pady=PAD)
        outer.pack(fill="both", expand=True)

        # Titel
        tk.Label(outer, text="Route controleren",
                 font=("Segoe UI", 15, "bold"), bg=BG).pack(anchor="w")
        tk.Label(outer,
                 text="Vink stops aan/uit, pas handelingstijden aan en stuur door naar RouteXL.",
                 font=("Segoe UI", 9), fg="#666", bg=BG).pack(anchor="w", pady=(2, 10))

        # Startadres
        self._build_address_block(outer, is_start=True)

        # Footer, inloggegevens en eindadres eerst aan de onderkant pakken zodat
        # de scrollbare stops-lijst de overgebleven ruimte vult en de footer altijd
        # volledig zichtbaar blijft.
        self._build_footer(outer, side="bottom")
        self._build_credentials(outer, side="bottom")
        self._build_address_block(outer, is_start=False, side="bottom")

        # Stops-lijst (scrollbaar) – vult resterende ruimte
        self._build_stops_list(outer)

    # ------------------------------------------------------------------
    # Start- / Eindadres-blok
    # ------------------------------------------------------------------

    def _build_address_block(self, parent, is_start: bool, side: str = "top"):
        label    = "Startadres" if is_start else "Eindadres"
        row_type = "start"      if is_start else "end"

        frame = tk.LabelFrame(parent, text=label,
                               font=("Segoe UI", 9, "bold"),
                               bg=BG, padx=8, pady=6)
        frame.pack(fill="x", pady=(0, 6), side=side)

        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x")

        stop = next((s for s in self.stops if s["row_type"] == row_type), None)

        if stop:
            plaats = stop['plaats']
            if not is_start:
                # Strips OCR artifacts like "02:21 (morgen)" that end up in plaats
                # when _extract_gewenst misses a time token followed by parenthetical text
                plaats = re.sub(r'\s*\d{1,2}[:.]\d{2}.*', '', plaats).strip()
            addr_text = f"{stop['straat']},  {stop['postcode']} {plaats}"
            tk.Label(row, text=addr_text,
                     font=("Segoe UI", 10), bg=BG).pack(side="left")

            if is_start:
                tk.Label(row, text="   Starttijd:", bg=BG,
                         font=("Segoe UI", 10)).pack(side="left")
                ttk.Entry(row, textvariable=self.start_time_var,
                          width=7).pack(side="left", padx=(4, 0))

            geo_lbl = tk.Label(row, text="⏳", font=("Segoe UI", 10),
                                fg="#9CA3AF", bg=BG)
            geo_lbl.pack(side="left", padx=(8, 0))
            stop["_geo_label"] = geo_lbl

            matched_lbl = tk.Label(frame, text="", font=("Segoe UI", 8, "italic"),
                                    fg="#6B7280", bg=BG)
            matched_lbl.pack(anchor="w", pady=(1, 0))
            stop["_matched_lbl"] = matched_lbl

            tk.Button(
                row, text="✎  Wijzig",
                font=("Segoe UI", 8),
                command=lambda s=stop, ie=is_start: self._edit_address(s, ie),
            ).pack(side="right")
        else:
            tk.Label(row, text="⚠  Niet gevonden in selectie",
                     fg="#D97706", font=("Segoe UI", 10), bg=BG).pack(side="left")
            tk.Button(
                row, text="+ Invoeren",
                font=("Segoe UI", 8),
                command=lambda ie=is_start: self._manual_address(ie),
            ).pack(side="right")

    # ------------------------------------------------------------------
    # Stops-lijst
    # ------------------------------------------------------------------

    def _build_stops_list(self, parent):
        outer = tk.LabelFrame(parent, text="Stops",
                               font=("Segoe UI", 9, "bold"),
                               bg=BG, padx=4, pady=4)
        outer.pack(fill="both", expand=True, pady=(0, 6))

        canvas = tk.Canvas(outer, bg=BG, borderwidth=0, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)

        self.list_frame = tk.Frame(canvas, bg=BG)
        self.list_frame.bind(
            "<Configure>",
            lambda _: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Muiswiel scrollen
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        nr = 0
        for stop in self.stops:
            if stop["row_type"] == "stop":
                nr += 1
                self._add_stop_row(stop, nr)

    def _add_stop_row(self, stop: dict, nr: int = 0):
        row_bg = "#FFFFFF"
        frame = tk.Frame(self.list_frame, bg=row_bg,
                         relief="flat", bd=0, pady=5, padx=6)
        frame.pack(fill="x", padx=3, pady=2)

        # Dunne scheidingslijn
        sep = tk.Frame(frame, bg="#E5E7EB", height=1)
        sep.pack(fill="x", side="bottom")

        # Rijnummer
        tk.Label(frame, text=f"{nr}.", width=3, anchor="e",
                 font=("Segoe UI", 9, "bold"), fg="#9CA3AF", bg=row_bg
                 ).pack(side="left", anchor="n", pady=4)

        # Checkbox
        checked = tk.BooleanVar(value=True)
        stop["_checked"] = checked

        chk = tk.Checkbutton(frame, variable=checked, bg=row_bg,
                              command=lambda f=frame, s=stop: self._toggle(f, s))
        chk.pack(side="left", anchor="n", pady=2)

        # Info-kolom
        info = tk.Frame(frame, bg=row_bg)
        info.pack(side="left", fill="x", expand=True)

        naam = stop.get("naam", "")
        if naam:
            tk.Label(info, text=naam, font=("Segoe UI", 10, "bold"),
                     bg=row_bg, anchor="w").pack(anchor="w")

        # Bewerkbare adresvelden
        addr_row = tk.Frame(info, bg=row_bg)
        addr_row.pack(anchor="w", pady=(1, 0))

        straat_var = tk.StringVar(value=stop.get("straat", ""))
        pc_var     = tk.StringVar(value=stop.get("postcode", ""))
        plaats_var = tk.StringVar(value=stop.get("plaats", ""))

        stop["_straat_var"] = straat_var
        stop["_pc_var"]     = pc_var
        stop["_plaats_var"] = plaats_var

        debounce_id = [None]

        def _make_cb(s, sv, pv, plv, d):
            def cb(*_):
                self._on_addr_change(s, sv, pv, plv, d)
            return cb

        cb = _make_cb(stop, straat_var, pc_var, plaats_var, debounce_id)
        straat_var.trace_add("write", cb)
        pc_var.trace_add("write", cb)
        plaats_var.trace_add("write", cb)

        ttk.Entry(addr_row, textvariable=straat_var, width=22).pack(side="left", padx=(0, 4))
        ttk.Entry(addr_row, textvariable=pc_var,     width=10).pack(side="left", padx=(0, 4))
        ttk.Entry(addr_row, textvariable=plaats_var, width=14).pack(side="left")

        # Onderste rij: tijdvenster + handelingstijd
        bot = tk.Frame(info, bg=row_bg)
        bot.pack(anchor="w", pady=(2, 0))

        gewenst_var = tk.StringVar(value=stop.get("gewenst", ""))
        stop["_gewenst_var"] = gewenst_var

        tk.Label(bot, text="🕐 Tijdvenster:",
                 font=("Segoe UI", 9), fg="#4B5563", bg=row_bg).pack(side="left")
        ttk.Entry(bot, textvariable=gewenst_var, width=14,
                  font=("Segoe UI", 9)).pack(side="left", padx=(3, 10))

        tk.Label(bot, text="Handelingstijd:",
                 font=("Segoe UI", 9), bg=row_bg).pack(side="left")

        svar = tk.StringVar(value="5")
        stop["_service_var"] = svar

        ttk.Entry(bot, textvariable=svar, width=4).pack(side="left", padx=3)
        tk.Label(bot, text="min", font=("Segoe UI", 9), bg=row_bg).pack(side="left")

        # Geocode-indicator (rechts)
        geo_lbl = tk.Label(frame, text="⏳",
                            font=("Segoe UI", 10), fg="#9CA3AF", bg=row_bg)
        geo_lbl.pack(side="right", anchor="n", padx=4)
        stop["_geo_label"] = geo_lbl

        matched_lbl = tk.Label(info, text="", font=("Segoe UI", 8, "italic"),
                                fg="#6B7280", bg=row_bg)
        matched_lbl.pack(anchor="w")
        stop["_matched_lbl"] = matched_lbl

    def _toggle(self, frame, stop):
        color = "#FFFFFF" if stop["_checked"].get() else "#F3F4F6"
        frame.configure(bg=color)
        for child in frame.winfo_children():
            try:
                child.configure(bg=color)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Inloggegevens
    # ------------------------------------------------------------------

    def _build_credentials(self, parent, side: str = "top"):
        frame = tk.LabelFrame(parent, text="RouteXL-account",
                               font=("Segoe UI", 9, "bold"),
                               bg=BG, padx=8, pady=6)
        frame.pack(fill="x", pady=(0, 6), side=side)

        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x")

        tk.Label(row, text="Gebruikersnaam:", bg=BG,
                 font=("Segoe UI", 10), width=16, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=self.username_var,
                  width=22).pack(side="left", padx=(0, 12))

        tk.Label(row, text="Wachtwoord:", bg=BG,
                 font=("Segoe UI", 10)).pack(side="left")
        ttk.Entry(row, textvariable=self.password_var,
                  show="*", width=22).pack(side="left", padx=(4, 0))

        row2 = tk.Frame(frame, bg=BG)
        row2.pack(fill="x", pady=(4, 0))
        tk.Label(row2, text="TomTom API-sleutel:", bg=BG,
                 font=("Segoe UI", 10), width=16, anchor="w").pack(side="left")
        ttk.Entry(row2, textvariable=self.tomtom_key_var,
                  width=46).pack(side="left", padx=(0, 4))
        tk.Label(row2, text="(optioneel, voor verkeersinfo)",
                 font=("Segoe UI", 8), fg="#9CA3AF", bg=BG).pack(side="left")

    # ------------------------------------------------------------------
    # Footer: knop + status
    # ------------------------------------------------------------------

    def _build_footer(self, parent, side: str = "top"):
        self.send_btn = tk.Button(
            parent,
            text="🚀   Doorzetten naar RouteXL",
            font=("Segoe UI", 12, "bold"),
            bg="#9CA3AF", fg="white",
            activebackground="#6B7280",
            padx=24, pady=10,
            relief="flat", cursor="arrow",
            state="disabled",
            command=self._send,
        )
        self.status_lbl = tk.Label(parent, textvariable=self.status_var,
                                    font=("Segoe UI", 9), fg="#6B7280", bg=BG)

        if side == "bottom":
            # Bij bottom-packing: status label eerst zodat het onderaan eindigt,
            # knop daarboven.
            self.status_lbl.pack(side="bottom", pady=(0, 4))
            self.send_btn.pack(side="bottom", pady=(4, 0))
        else:
            self.send_btn.pack(pady=(4, 2))
            self.status_lbl.pack()

    # ------------------------------------------------------------------
    # Adres bewerken / handmatig invoeren
    # ------------------------------------------------------------------

    def _edit_address(self, stop: dict, is_start: bool):
        current = f"{stop['straat']}, {stop['postcode']} {stop['plaats']}"
        new = simpledialog.askstring(
            "Adres wijzigen",
            "Straat,  Postcode  Plaats\n(komma als scheidingsteken):",
            initialvalue=current,
            parent=self.win,
        )
        if not new:
            return
        parts = [p.strip() for p in new.split(",")]
        if len(parts) >= 2:
            stop["straat"] = parts[0]
            rest = parts[1].split()
            stop["postcode"] = rest[0] if rest else ""
            stop["plaats"]   = " ".join(rest[1:]) if len(rest) > 1 else (parts[2] if len(parts) > 2 else "")
        # Hergeocoder
        self._geocode_one(stop)

    def _manual_address(self, is_start: bool):
        new = simpledialog.askstring(
            "Adres invoeren",
            "Straat,  Postcode  Plaats:",
            parent=self.win,
        )
        if not new:
            return
        parts = [p.strip() for p in new.split(",")]
        rest  = parts[1].split() if len(parts) > 1 else []
        stop = {
            "naam":         "Startadres" if is_start else "Eindadres",
            "straat":       parts[0] if parts else "",
            "postcode":     rest[0] if rest else "",
            "plaats":       " ".join(rest[1:]) if len(rest) > 1 else (parts[2] if len(parts) > 2 else ""),
            "gewenst":      "",
            "row_type":     "start" if is_start else "end",
            "lat": None, "lon": None,
        }
        self.stops.append(stop)
        self._geocode_one(stop)

    # ------------------------------------------------------------------
    # Geocodering
    # ------------------------------------------------------------------

    def _on_addr_change(self, stop: dict, straat_var, pc_var, plaats_var, debounce_id: list):
        if debounce_id[0] is not None:
            self.win.after_cancel(debounce_id[0])
        stop["straat"]   = straat_var.get()
        stop["postcode"] = pc_var.get()
        stop["plaats"]   = plaats_var.get()
        debounce_id[0] = self.win.after(800, lambda: self._geocode_one(stop))

    def _geocode_one(self, stop: dict):
        def task():
            lat, lon, matched, pc, pl = geocode(stop["straat"], stop["postcode"], stop["plaats"])
            stop["lat"], stop["lon"] = lat, lon
            self.win.after(0, lambda s=stop, ok=bool(lat), m=matched, p=pc, pl2=pl:
                           self._set_geo_label(s, ok, m, p, pl2))
        threading.Thread(target=task, daemon=True).start()

    def _start_geocoding(self):
        targets = [s for s in self.stops if s.get("straat")]

        def task():
            for i, stop in enumerate(targets):
                self.win.after(0, lambda n=i+1, t=len(targets), s=stop:
                               self.status_var.set(
                                   f"Geocoderen {n}/{t}: {s.get('straat', '')}…"))
                lat, lon, matched, pc, pl = geocode(stop["straat"], stop["postcode"], stop["plaats"])
                stop["lat"], stop["lon"] = lat, lon
                self.win.after(0, lambda s=stop, ok=bool(lat), m=matched, p=pc, pl2=pl:
                               self._set_geo_label(s, ok, m, p, pl2))

            self.win.after(0, lambda: self._on_geocoding_done(len(targets)))

        threading.Thread(target=task, daemon=True).start()

    def _on_geocoding_done(self, n: int):
        self.status_var.set(f"✓ Geocodering voltooid ({n} adressen)")
        self.send_btn.config(
            state="normal",
            bg="#16A34A",
            activebackground="#15803D",
            cursor="hand2",
        )

    def _set_geo_label(self, stop: dict, ok: bool, matched: str | None = None,
                       fixed_pc: str | None = None, fixed_plaats: str | None = None):
        lbl = stop.get("_geo_label")
        if lbl and lbl.winfo_exists():
            lbl.config(text="✓" if ok else "✗",
                       fg="#16A34A" if ok else "#DC2626")

        # Sla het geocodeerde adres op voor gebruik in RouteXL-payload en resultaatscherm
        if ok and matched:
            stop["matched_address"] = matched
            # Extraheer straatnaam+huisnummer (eerste deel vóór de komma).
            # Alleen overschrijven als de huidige straat leeg is of een kaal huisnummer,
            # zodat "Noordstraat 73" nooit wordt vervangen door "73" wanneer Nominatim
            # het adres in huisnummer-eerst-formaat teruggeeft.
            clean_straat = matched.split(",")[0].strip()
            old_straat   = stop.get("straat", "")
            bare_number  = bool(re.match(r"^\d+[a-zA-Z]?$", old_straat.strip()))
            if clean_straat and clean_straat != old_straat and (not old_straat or bare_number):
                stop["straat"] = clean_straat
                sv = stop.get("_straat_var")
                if sv:
                    sv.set(clean_straat)

        # Corrigeer postcode/plaats in de UI als geocoder ze hersteld heeft
        if ok and fixed_pc and fixed_pc != stop.get("postcode"):
            stop["postcode"] = fixed_pc
            pc_var = stop.get("_pc_var")
            if pc_var:
                pc_var.set(fixed_pc)
        if ok and fixed_plaats and fixed_plaats != stop.get("plaats"):
            stop["plaats"] = fixed_plaats
            pl_var = stop.get("_plaats_var")
            if pl_var:
                pl_var.set(fixed_plaats)

        matched_lbl = stop.get("_matched_lbl")
        if matched_lbl and matched_lbl.winfo_exists():
            if ok and matched:
                short = matched[:80] + "…" if len(matched) > 80 else matched
                matched_lbl.config(text=f"↳ {short}", fg="#6B7280")
            elif not ok:
                matched_lbl.config(text="↳ Adres niet gevonden", fg="#DC2626")

    # ------------------------------------------------------------------
    # Verzenden
    # ------------------------------------------------------------------

    def _send(self):
        username   = self.username_var.get().strip()
        password   = self.password_var.get().strip()
        start_time = self.start_time_var.get().strip()

        if not username or not password:
            messagebox.showerror("Fout",
                "Voer je RouteXL-gebruikersnaam en wachtwoord in.",
                parent=self.win)
            return

        if not re.match(r"^\d{1,2}:\d{2}$", start_time):
            messagebox.showerror("Fout",
                "Voer een geldige starttijd in (bijv. 09:00).",
                parent=self.win)
            return

        # Stel service_time en tijdvenster in vanuit UI
        for stop in self.stops:
            if "_service_var" in stop:
                try:
                    stop["service_time"] = int(stop["_service_var"].get())
                except ValueError:
                    stop["service_time"] = 5
            if "_gewenst_var" in stop:
                stop["gewenst"] = stop["_gewenst_var"].get().strip()

        # Bouw volgorde: start → geselecteerde stops → eind
        start_stops = [s for s in self.stops if s["row_type"] == "start"]
        sel_stops   = [s for s in self.stops
                       if s["row_type"] == "stop"
                       and s.get("_checked") and s["_checked"].get()]
        end_stops   = [s for s in self.stops if s["row_type"] == "end"]

        ordered = start_stops + sel_stops + end_stops

        if len(ordered) < 2:
            messagebox.showerror("Fout",
                "Minimaal 2 adressen (start + ten minste één stop) nodig.",
                parent=self.win)
            return

        # Waarschuw over ontbrekende geocodes
        no_geo = [s for s in ordered if not s.get("lat")]
        if no_geo:
            namen = ", ".join(s.get("straat", "?") for s in no_geo[:3])
            if not messagebox.askyesno(
                "Ontbrekende coördinaten",
                f"De volgende adressen konden niet worden gecodeerd:\n{namen}\n\n"
                "Ze worden overgeslagen. Toch doorzetten?",
                parent=self.win,
            ):
                return

        try:
            locations = build_locations(ordered, start_time)
        except Exception as e:
            messagebox.showerror("Fout", f"Fout bij opbouwen route:\n{e}",
                                 parent=self.win)
            return

        if len(locations) < 2:
            messagebox.showerror("Fout",
                "Onvoldoende geocodeerbare adressen om een route te sturen.",
                parent=self.win)
            return

        # Bewaar voor geforceerde herberekening
        self._last_ordered    = ordered
        self._last_start_time = start_time
        self._last_username   = username
        self._last_password   = password
        self._last_tomtom_key = self.tomtom_key_var.get().strip()

        # Tijdvensters per adres (voor resultaatscherm) – zelfde sleutel als build_locations
        windows_map: dict[str, str] = {}
        for s in ordered:
            addr = (s.get("matched_address")
                    or f"{s['straat']}, {s['postcode']} {s['plaats']}")
            if s.get("gewenst"):
                windows_map[addr] = s["gewenst"]

        self.send_btn.config(state="disabled",
                              text="Bezig met verzenden…")
        self.status_var.set("Route verzenden naar RouteXL…")

        tk_key = self.tomtom_key_var.get().strip()
        save_tomtom_key(tk_key)

        def task():
            try:
                result = send_route(username, password, locations)
                save_credentials(username, password)
                self.win.after(0, lambda r=result: self._show_result(
                    r, start_time, locations, windows_map, tk_key))
            except Exception as e:
                # Capture nu — Python 3 zet 'e' op None aan het eind van except-blok,
                # waardoor een gewone lambda altijd str(None)="None" zou tonen.
                msg = str(e)
                self.win.after(0, lambda m=msg: messagebox.showerror(
                    "RouteXL-fout", m, parent=self.win))
            finally:
                self.win.after(0, lambda: self.send_btn.config(
                    state="normal",
                    text="🚀   Doorzetten naar RouteXL"))

        threading.Thread(target=task, daemon=True).start()

    @staticmethod
    def _parse_minutes(time_str: str) -> int:
        """'09:30' of '9.30' → 570"""
        ts = time_str.strip().replace(".", ":")
        h, m = map(int, ts.split(":"))
        return h * 60 + m

    def _show_result(self, result: dict, start_time: str, locations: list[dict],
                     windows_map: dict | None = None, tomtom_key: str = ""):
        self.status_var.set("✓ Route ontvangen van RouteXL")

        route    = result.get("route") or {}
        feasible = result.get("feasible", True)
        start_min = sum(int(x) * m for x, m in zip(start_time.split(":"), [60, 1])) if start_time else 0

        # Sorteer waypoints op aankomsttijd (sleutels zijn al in volgorde)
        waypoints = sorted(route.values(), key=lambda w: int(w.get("arrival", 0)))

        # Bouw lat/lon-opzoekmap vanuit de verzonden locations (op adres)
        coord_map = {loc["address"]: (loc["lat"], loc["lng"]) for loc in locations}

        win = tk.Toplevel(self.win)
        win.title("Geoptimaliseerde route – RouteXL")
        win.geometry("640x800")
        win.configure(bg=BG)
        win.resizable(True, True)

        outer = tk.Frame(win, bg=BG, padx=PAD, pady=PAD)
        outer.pack(fill="both", expand=True)

        # Koptekst
        tk.Label(outer, text="Geoptimaliseerde route",
                 font=("Segoe UI", 14, "bold"), bg=BG).pack(anchor="w")
        kleur = "#16A34A" if feasible else "#DC2626"
        tk.Label(outer,
                 text="✓ Route is haalbaar" if feasible else "⚠ Niet alle tijdvensters zijn haalbaar",
                 font=("Segoe UI", 9), fg=kleur, bg=BG).pack(anchor="w", pady=(0, 8))

        # Scrollbare lijst
        frame = tk.Frame(outer, bg=BG)
        frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(frame, bg=BG, borderwidth=0, highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>",
                   lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        # distance per waypoint is cumulatief (km vanaf start) → totaal = laatste waarde
        total_km = float(waypoints[-1].get("distance", 0)) if waypoints else 0.0
        late_addresses: set[str] = set()  # adressen >30 min te laat
        tomtom_labels: dict[str, tk.Label] = {}

        for i, wp in enumerate(waypoints):
            arr    = start_min + int(wp.get("arrival", 0))
            dist   = float(wp.get("distance", 0))
            naam   = wp.get("name", "–")

            # Bepaal tijdvenster-kleur en overschrijding
            gewenst      = (windows_map or {}).get(naam, "")
            arr_color    = "#1D4ED8"
            violation    = 0
            has_window   = False
            window_label = ""

            if gewenst:
                m_win = re.search(
                    r"(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})", gewenst)
                if m_win:
                    has_window = True
                    ready_abs  = self._parse_minutes(m_win.group(1))
                    due_abs    = self._parse_minutes(m_win.group(2))
                    if due_abs <= ready_abs:   # venster over middernacht
                        due_abs += 24 * 60
                    violation  = arr - due_abs
                    if violation > 30:
                        arr_color = "#DC2626"
                        late_addresses.add(naam)
                        window_label = f"⚠ {violation} min te laat  (venster: {gewenst})"
                    elif violation > 0:
                        arr_color = "#D97706"
                        window_label = f"⚠ {violation} min te laat  (venster: {gewenst})"
                    else:
                        arr_color = "#16A34A"
                        window_label = f"✓  {gewenst}"

            row = tk.Frame(inner, bg=BG, pady=3)
            row.pack(fill="x", padx=4)

            tk.Label(row, text=f"{i+1:>2}.", width=3,
                     font=("Segoe UI", 10, "bold"), bg=BG, anchor="e").pack(side="left")
            tk.Label(row, text=naam,
                     font=("Segoe UI", 10), bg=BG, anchor="w",
                     wraplength=300, justify="left").pack(side="left", padx=(6, 0))

            rechts = tk.Frame(row, bg=BG)
            rechts.pack(side="right")
            tk.Label(rechts, text=_fmt_arrival(arr),
                     font=("Segoe UI", 10, "bold"), fg=arr_color, bg=BG, width=8).pack(side="left")
            if dist > 0:
                tk.Label(rechts, text=f"{dist:.1f} km",
                         font=("Segoe UI", 9), fg="#6B7280", bg=BG, width=8).pack(side="left")

            if has_window:
                lbl_color = arr_color if violation > 0 else "#6B7280"
                tk.Label(inner, text=f"   {window_label}",
                         font=("Segoe UI", 8), fg=lbl_color, bg=BG, anchor="w"
                         ).pack(fill="x", padx=4)

            if tomtom_key:
                tt_lbl = tk.Label(inner, text="",
                                  font=("Segoe UI", 8), fg="#6B7280", bg=BG, anchor="w")
                tt_lbl.pack(fill="x", padx=16)
                tomtom_labels[naam] = tt_lbl

            tk.Frame(inner, bg="#E5E7EB", height=1).pack(fill="x", padx=4)

        tk.Label(outer, text=f"Totaal: {total_km:.1f} km",
                 font=("Segoe UI", 10, "bold"), bg=BG).pack(anchor="e", pady=(6, 0))

        # TomTom verkeerssectie
        if tomtom_key:
            tt_frame = tk.LabelFrame(outer, text="🚦 Verkeerssituatie (TomTom)",
                                      font=("Segoe UI", 9, "bold"),
                                      bg=BG, padx=8, pady=4)
            tt_frame.pack(fill="x", pady=(6, 0))
            tt_scroll = tk.Scrollbar(tt_frame, orient="vertical")
            tt_inc_lbl = tk.Text(
                tt_frame, height=5, font=("Segoe UI", 9), fg="#9CA3AF", bg=BG,
                wrap="word", relief="flat", bd=0, cursor="arrow",
                yscrollcommand=tt_scroll.set,
            )
            tt_scroll.config(command=tt_inc_lbl.yview)
            tt_inc_lbl.insert(1.0, "Verkeersinformatie laden…")
            tt_inc_lbl.config(state="disabled")
            tt_scroll.pack(side="right", fill="y")
            tt_inc_lbl.pack(fill="x", expand=True)

            tomtom_wps: list[tuple[float, float, str]] = []
            for wp in waypoints:
                wp_naam = wp.get("name", "")
                coord   = coord_map.get(wp_naam)
                if coord:
                    try:
                        tomtom_wps.append((float(coord[0]), float(coord[1]), wp_naam))
                    except (ValueError, TypeError):
                        pass

            if len(tomtom_wps) >= 2:
                threading.Thread(
                    target=self._fetch_tomtom_traffic,
                    args=(win, tomtom_key, tomtom_wps, start_time,
                          tomtom_labels, tt_inc_lbl),
                    daemon=True,
                ).start()
            else:
                tt_inc_lbl.config(text="Onvoldoende coördinaten voor TomTom.")

        # Forceer-knop als er stops >30 min te laat zijn
        if late_addresses:
            force_frame = tk.Frame(outer, bg="#FEF3C7", relief="flat", bd=1, padx=8, pady=6)
            force_frame.pack(fill="x", pady=(6, 0))
            tk.Label(force_frame,
                     text=f"⚠  {len(late_addresses)} stop(s) kunnen niet op tijd worden bereikt."
                          "  Herbereken zonder tijdvenster voor deze stops,"
                          " zodat RouteXL ze op het best mogelijke moment inplant.",
                     font=("Segoe UI", 9), fg="#92400E", bg="#FEF3C7",
                     wraplength=520, justify="left").pack(anchor="w")
            tk.Button(
                force_frame,
                text="🔄  Herbereken zonder tijdvenster voor te-late stops",
                font=("Segoe UI", 9, "bold"),
                bg="#D97706", fg="white",
                activebackground="#B45309",
                relief="flat", padx=12, pady=5, cursor="hand2",
                command=lambda la=late_addresses, w=win: self._force_route(la, w),
            ).pack(anchor="w", pady=(4, 0))

        # Knoppen
        btn_row = tk.Frame(outer, bg=BG)
        btn_row.pack(pady=(10, 0))

        tk.Button(btn_row, text="🗺  Open kaart",
                  font=("Segoe UI", 10), bg="#2563EB", fg="white",
                  activebackground="#1D4ED8", relief="flat", padx=16, pady=8,
                  cursor="hand2",
                  command=lambda: self._open_route_map(waypoints, coord_map, start_min)
                  ).pack(side="left", padx=(0, 8))

        tk.Button(btn_row, text="Sluit",
                  font=("Segoe UI", 10), relief="flat", padx=16, pady=8,
                  command=win.destroy).pack(side="left")

    # ------------------------------------------------------------------
    # Geforceerde herberekening (tijdvensters te-late stops verwijderd)
    # ------------------------------------------------------------------

    def _force_route(self, late_addresses: set[str], prev_win: tk.Toplevel):
        ordered    = getattr(self, "_last_ordered",    None)
        start_time = getattr(self, "_last_start_time", "")
        username   = getattr(self, "_last_username",   "")
        password   = getattr(self, "_last_password",   "")
        tk_key     = getattr(self, "_last_tomtom_key", "")

        if not ordered:
            return

        locations: list[dict] = []
        windows_map: dict[str, str] = {}
        for stop in ordered:
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
            # Laat tijdvenster weg voor te-late stops
            if address not in late_addresses:
                restr = calculate_restrictions(start_time, stop.get("gewenst", ""))
                if restr:
                    loc["restrictions"] = restr
            locations.append(loc)
            if stop.get("gewenst"):
                windows_map[address] = stop["gewenst"]

        prev_win.destroy()
        self.send_btn.config(state="disabled", text="Bezig met verzenden…")
        self.status_var.set("Geforceerde route verzenden naar RouteXL…")

        def task():
            try:
                result = send_route(username, password, locations)
                self.win.after(0, lambda r=result: self._show_result(
                    r, start_time, locations, windows_map, tk_key))
            except Exception as e:
                msg = str(e)
                self.win.after(0, lambda m=msg: messagebox.showerror(
                    "RouteXL-fout", m, parent=self.win))
            finally:
                self.win.after(0, lambda: self.send_btn.config(
                    state="normal", text="🚀   Doorzetten naar RouteXL"))

        threading.Thread(target=task, daemon=True).start()

    # ------------------------------------------------------------------
    # TomTom verkeersinformatie ophalen (achtergrond-thread)
    # ------------------------------------------------------------------

    def _fetch_tomtom_traffic(
        self,
        win: tk.Toplevel,
        api_key: str,
        waypoints: list[tuple[float, float, str]],
        start_time: str,
        tomtom_labels: dict,
        inc_lbl: tk.Label,
    ) -> None:
        """Haalt TomTom verkeersdata op en werkt het resultaatscherm bij."""
        def _set_inc(text: str, color: str) -> None:
            if not inc_lbl.winfo_exists():
                return
            inc_lbl.config(state="normal")
            inc_lbl.delete(1.0, tk.END)
            inc_lbl.insert(1.0, text)
            inc_lbl.config(state="disabled", fg=color)

        try:
            traffic   = get_route_with_traffic(api_key, waypoints, start_time)
            incidents = get_incidents_on_route(api_key, waypoints)
        except Exception as e:
            err = str(e)
            win.after(0, lambda: _set_inc(f"TomTom niet beschikbaar: {err}", "#DC2626"))
            return

        for entry in traffic:
            lbl = tomtom_labels.get(entry["address"])
            if not lbl:
                continue
            delay_min = round(entry["delay_sec"] / 60)
            if delay_min >= 20:
                text, color = f"🚗 TomTom: +{delay_min} min file op dit traject", "#DC2626"
            elif delay_min >= 5:
                text, color = f"🚗 TomTom: +{delay_min} min file op dit traject", "#D97706"
            elif delay_min > 0:
                text, color = f"🚗 TomTom: +{delay_min} min", "#6B7280"
            else:
                text, color = "🚗 TomTom: geen file", "#16A34A"
            win.after(0, lambda l=lbl, t=text, c=color:
                      l.winfo_exists() and l.config(text=t, fg=c))

        self._tomtom_incidents = incidents
        if incidents:
            inc_text  = "\n".join(f"• {inc['text']}" for inc in incidents)
            inc_color = "#DC2626"
        else:
            inc_text  = "✓ Geen incidenten in het routegebied"
            inc_color = "#16A34A"
        win.after(0, lambda t=inc_text, c=inc_color: _set_inc(t, c))

    # ------------------------------------------------------------------
    # Kaart genereren (Leaflet / OpenStreetMap, lokale HTML)
    # ------------------------------------------------------------------

    def _open_route_map(self, waypoints: list, coord_map: dict, start_min: int):
        pts = []
        for i, wp in enumerate(waypoints):
            naam  = wp.get("name", "")
            coord = coord_map.get(naam)
            if not coord:
                continue
            arr   = start_min + int(wp.get("arrival", 0))
            dist  = float(wp.get("distance", 0))
            pts.append({
                "nr":    i + 1,
                "lat":   float(coord[0]),
                "lon":   float(coord[1]),
                "naam":  naam.replace("'", "\\'"),
                "tijd":  _fmt_arrival(arr),
                "km":    f"{dist:.1f}",
            })

        if not pts:
            messagebox.showwarning("Kaart", "Geen coördinaten beschikbaar.", parent=self.win)
            return

        center_lat = sum(p["lat"] for p in pts) / len(pts)
        center_lon = sum(p["lon"] for p in pts) / len(pts)
        markers_js = "\n".join(
            f"addStop({p['lat']}, {p['lon']}, {p['nr']}, '{p['naam']}', '{p['tijd']}', '{p['km']} km');"
            for p in pts
        )
        latlngs_js = ", ".join(f"[{p['lat']}, {p['lon']}]" for p in pts)

        # OSRM waypoints: lon,lat;lon,lat;...
        osrm_coords = ";".join(f"{p['lon']},{p['lat']}" for p in pts)
        osrm_url = (
            f"https://router.project-osrm.org/route/v1/driving/{osrm_coords}"
            "?overview=full&geometries=geojson"
        )

        incidents_js = "\n".join(
            f"addIncident({inc['lat']}, {inc['lon']}, "
            f"'{inc['text'].replace(chr(39), ' ')}', {inc['severity']});"
            for inc in self._tomtom_incidents
        )

        html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="utf-8">
<title>Route – Mendrix → RouteXL</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body {{ margin: 0; font-family: Segoe UI, sans-serif; }}
  #map {{ height: 100vh; }}
  #status {{ position: absolute; top: 10px; left: 50%; transform: translateX(-50%);
             z-index: 1000; background: rgba(255,255,255,.9); padding: 6px 14px;
             border-radius: 20px; font-size: 13px; box-shadow: 0 1px 4px rgba(0,0,0,.3); }}
  .stop-icon {{ background: #2563EB; color: #fff; border-radius: 50%;
               width: 26px; height: 26px; display: flex; align-items: center;
               justify-content: center; font-weight: bold; font-size: 12px;
               border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,.4); }}
  .stop-icon.first {{ background: #16A34A; }}
  .stop-icon.last  {{ background: #DC2626; }}
  .incident-icon {{ color: #fff; border-radius: 4px; width: 24px; height: 24px;
               display: flex; align-items: center; justify-content: center;
               font-size: 14px; border: 2px solid #fff;
               box-shadow: 0 1px 4px rgba(0,0,0,.4); }}
</style>
</head>
<body>
<div id="map"></div>
<div id="status">Route laden via wegen…</div>
<script>
var map = L.map('map').setView([{center_lat}, {center_lon}], 8);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 19
}}).addTo(map);

var total = {len(pts)};
var bounds = [];

function addStop(lat, lon, nr, naam, tijd, km) {{
  var cls = nr === 1 ? 'first' : (nr === total ? 'last' : '');
  var icon = L.divIcon({{
    className: '',
    html: '<div class="stop-icon ' + cls + '">' + nr + '</div>',
    iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -14]
  }});
  var label = '<b>' + nr + '. ' + naam + '</b><br>Aankomst: <b>' + tijd + '</b>';
  if (km !== '0.0 km') label += '<br>' + km;
  L.marker([lat, lon], {{icon: icon}}).bindPopup(label).addTo(map);
  bounds.push([lat, lon]);
}}

function addIncident(lat, lon, text, severity) {{
  var color = severity >= 4 ? '#DC2626' : '#D97706';
  var icon = L.divIcon({{
    className: '',
    html: '<div class="incident-icon" style="background:' + color + '">⚠</div>',
    iconSize: [24, 24], iconAnchor: [12, 12], popupAnchor: [0, -13]
  }});
  L.marker([lat, lon], {{icon: icon}}).bindPopup('<b>⚠ ' + text + '</b>').addTo(map);
}}

{markers_js}
{incidents_js}
map.fitBounds(bounds, {{padding: [40, 40]}});

// Haal weggebaseerde route op via OSRM
fetch('{osrm_url}')
  .then(function(r) {{ return r.json(); }})
  .then(function(data) {{
    var coords = data.routes[0].geometry.coordinates;
    // OSRM geeft [lon, lat]; Leaflet wil [lat, lon]
    var latlngs = coords.map(function(c) {{ return [c[1], c[0]]; }});
    L.polyline(latlngs, {{color: '#2563EB', weight: 4, opacity: 0.8}}).addTo(map);
    document.getElementById('status').style.display = 'none';
  }})
  .catch(function() {{
    // Fallback bij geen internet: rechte lijnen
    L.polyline([{latlngs_js}], {{color: '#2563EB', weight: 3, opacity: 0.6, dashArray: '8 6'}}).addTo(map);
    document.getElementById('status').textContent = 'Wegen niet beschikbaar – rechte lijnen getoond';
    setTimeout(function() {{ document.getElementById('status').style.display='none'; }}, 4000);
  }});
</script>
</body>
</html>"""

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8",
            prefix="mendrix_route_"
        )
        tmp.write(html)
        tmp.close()
        webbrowser.open(f"file:///{tmp.name}")

        # Verwijder het bestand na 60 seconden — browser heeft het dan al geladen
        def _cleanup(path=tmp.name):
            try:
                os.unlink(path)
            except OSError:
                pass
        threading.Timer(60, _cleanup).start()
