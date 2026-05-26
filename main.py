"""
main.py
Mendrix → RouteXL  –  hoofdvenster en Mendrix-detectie.

Start: python main.py
"""

import os
import sys
import time
import threading
import tkinter as tk
from tkinter import messagebox

# Tesseract automatisch vinden op Windows
import pytesseract

def _find_tesseract() -> str | None:
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
    ]
    return next((p for p in candidates if os.path.exists(p)), None)

tess = _find_tesseract()
if tess:
    pytesseract.pytesseract.tesseract_cmd = tess

try:
    import win32gui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

from overlay import SelectionOverlay
from ocr_parser import parse_screenshot, validate_stops
from review_window import ReviewWindow

# ---------------------------------------------------------------------------
# Mendrix venster opzoeken
# ---------------------------------------------------------------------------

def find_mendrix() -> tuple[int | None, str | None]:
    """Zoek een zichtbaar venster met 'mendrix' in de titel."""
    if not HAS_WIN32:
        return None, None
    result: list = [None, None]

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if "mendrix" in title.lower():
                result[0] = hwnd
                result[1] = title

    win32gui.EnumWindows(cb, None)
    return result[0], result[1]




# ---------------------------------------------------------------------------
# Hoofd-applicatie
# ---------------------------------------------------------------------------

class App:
    POLL_INTERVAL_MS = 2000  # ms tussen Mendrix-checks

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Mendrix → RouteXL")
        self.root.geometry("420x230")
        self.root.resizable(False, False)
        self.root.configure(bg="#F9FAFB")

        self._build_ui()
        self._check_tesseract()
        self._poll_mendrix()   # start polling (tkinter after-loop)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        bg = "#F9FAFB"
        outer = tk.Frame(self.root, bg=bg, padx=18, pady=16)
        outer.pack(fill="both", expand=True)

        # Status-rij
        status_row = tk.Frame(outer, bg=bg)
        status_row.pack(fill="x")

        self.dot_lbl = tk.Label(status_row, text="●",
                                 font=("Segoe UI", 20), fg="#9CA3AF", bg=bg)
        self.dot_lbl.pack(side="left")

        self.status_lbl = tk.Label(status_row,
                                    text="Mendrix zoeken…",
                                    font=("Segoe UI", 11), bg=bg)
        self.status_lbl.pack(side="left", padx=10)

        # Sub-tekst
        self.sub_lbl = tk.Label(outer,
                                 text="Open Mendrix om te beginnen.",
                                 font=("Segoe UI", 9), fg="#6B7280", bg=bg,
                                 wraplength=380, justify="left")
        self.sub_lbl.pack(anchor="w", pady=(6, 0))

        # Knop
        self.btn = tk.Button(
            outer,
            text="🖱   Start Selectie",
            font=("Segoe UI", 12, "bold"),
            bg="#2563EB", fg="white",
            activebackground="#1D4ED8",
            padx=22, pady=10,
            state="disabled",
            relief="flat", cursor="hand2",
            command=self._start_selection,
        )
        self.btn.pack(pady=20)

        # Versie
        tk.Label(outer, text="v1.0  ·  Mendrix → RouteXL",
                 font=("Segoe UI", 8), fg="#D1D5DB", bg=bg).pack(side="bottom")

    # ------------------------------------------------------------------
    # Tesseract check
    # ------------------------------------------------------------------

    def _check_tesseract(self):
        if not _find_tesseract():
            messagebox.showwarning(
                "Tesseract niet gevonden",
                "Tesseract OCR is niet geïnstalleerd of niet gevonden.\n\n"
                "Download via:\n"
                "https://github.com/UB-Mannheim/tesseract/wiki\n\n"
                "Installeer en herstart de app.",
                parent=self.root,
            )

    # ------------------------------------------------------------------
    # Mendrix polling (tkinter after-loop, thread-safe)
    # ------------------------------------------------------------------

    def _poll_mendrix(self):
        hwnd, title = find_mendrix()

        if not HAS_WIN32:
            self._set_status("pywin32 niet geïnstalleerd", "#D97706")
            self.sub_lbl.config(text="Voer 'pip install pywin32' uit.")
        elif hwnd:
            self._set_status("Mendrix actief", "#16A34A")
            self.sub_lbl.config(
                text="Zorg dat het Ritten scherm zichtbaar is, klik dan 'Start Selectie' "
                     "en teken een rechthoek om de gewenste rijen.")
            self.btn.config(state="normal")
        else:
            self._set_status("Mendrix niet gevonden", "#DC2626")
            self.sub_lbl.config(
                text="Open Mendrix en ga naar het Ritten scherm.")
            self.btn.config(state="disabled")

        self.root.after(self.POLL_INTERVAL_MS, self._poll_mendrix)

    def _set_status(self, text: str, color: str):
        self.status_lbl.config(text=text)
        self.dot_lbl.config(fg=color)

    # ------------------------------------------------------------------
    # Selectie starten
    # ------------------------------------------------------------------

    def _start_selection(self):
        overlay = SelectionOverlay(self.root, self._on_screenshot)
        overlay.show()

    def _on_screenshot(self, screenshot, _bbox):

        if screenshot is None:
            return  # Gebruiker heeft geannuleerd

        # Parse OCR in achtergrond-thread zodat UI niet blokkeert
        def task():
            stops = parse_screenshot(screenshot)
            ok, msg = validate_stops(stops)
            if not ok:
                self.root.after(0, lambda: messagebox.showerror(
                    "Niet herkend",
                    f"{msg}\n\nZorg dat de kolommen Straat, Postcode en Plaats "
                    "in de selectie staan.",
                    parent=self.root,
                ))
                return
            self.root.after(0, lambda: ReviewWindow(self.root, stops))

        threading.Thread(target=task, daemon=True).start()

    # ------------------------------------------------------------------

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    App().run()
