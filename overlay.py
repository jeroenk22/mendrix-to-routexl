"""
overlay.py
Volledig-scherm overlay die eerst een screenshot maakt en dat als achtergrond
toont. De gebruiker tekent een rechthoek over de gewenste rijen in Mendrix.

Gebruik:
    SelectionOverlay(root, callback).show()

callback(screenshot: Image | None, bbox: tuple | None)
"""

import ctypes
import time
import tkinter as tk
from PIL import ImageGrab, ImageTk

_SM_XVIRTUALSCREEN  = 76
_SM_YVIRTUALSCREEN  = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


def _get_virtual_screen() -> tuple[int, int, int, int]:
    """Geeft (x, y, breedte, hoogte) van het virtuele scherm (alle monitors samen)."""
    u = ctypes.windll.user32
    return (
        u.GetSystemMetrics(_SM_XVIRTUALSCREEN),
        u.GetSystemMetrics(_SM_YVIRTUALSCREEN),
        u.GetSystemMetrics(_SM_CXVIRTUALSCREEN),
        u.GetSystemMetrics(_SM_CYVIRTUALSCREEN),
    )


class SelectionOverlay:
    def __init__(self, root: tk.Tk, callback):
        self.root     = root
        self.callback = callback
        self._sx = self._sy = 0
        self._rect_id = None

    def show(self):
        """Maak volledig-scherm screenshot, toon als overlay, wacht op selectie."""
        # Geef Mendrix de tijd om zichtbaar te zijn voordat we de screenshot maken
        self.root.after(300, self._capture_and_show)

    def _capture_and_show(self):
        # Verberg hoofdvenster zodat het niet op de screenshot staat
        self.root.iconify()
        self.root.update()
        time.sleep(0.15)

        vx, vy, vw, vh = _get_virtual_screen()

        try:
            # all_screens=True pakt alle monitors tegelijk
            self._full_shot = ImageGrab.grab(all_screens=True)
        except Exception as ex:
            print(f"Screenshot fout: {ex}")
            self.root.deiconify()
            self.callback(None, None)
            return

        self.win = tk.Toplevel(self.root)
        self.win.attributes("-topmost", True)
        self.win.overrideredirect(True)
        # Positioneer over alle monitors
        self.win.geometry(f"{vw}x{vh}+{vx}+{vy}")

        sw, sh = vw, vh

        # Screenshot als achtergrond (is al exact vw×vh groot)
        self._bg_img = ImageTk.PhotoImage(self._full_shot)

        self.canvas = tk.Canvas(
            self.win,
            width=sw, height=sh,
            cursor="crosshair",
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, anchor="nw", image=self._bg_img)

        # Donkere overlay zodat selectie duidelijk zichtbaar is
        self.canvas.create_rectangle(0, 0, sw, sh,
                                     fill="black", stipple="gray25",
                                     outline="")

        # Instructie-tekst
        self.canvas.create_text(
            sw // 2, 28,
            text="Selecteer vanaf de kolom 'Straat' t/m 'Gewenst'  —  sla de kolom 'Naam' links over",
            fill="white",
            font=("Segoe UI", 12, "bold"),
        )
        self.canvas.create_text(
            sw // 2, 54,
            text="ESC = annuleren",
            fill="#CCCCCC",
            font=("Segoe UI", 10),
        )

        self.canvas.bind("<ButtonPress-1>",   self._on_press)
        self.canvas.bind("<B1-Motion>",       self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.win.bind("<Escape>",             self._on_escape)

        self.win.focus_force()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def _on_press(self, e):
        self._sx, self._sy = e.x, e.y
        if self._rect_id:
            self.canvas.delete(self._rect_id)

    def _on_drag(self, e):
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            self._sx, self._sy, e.x, e.y,
            outline="#FF3B30", width=3,
            fill="#007AFF", stipple="gray12",
        )

    def _on_release(self, e):
        x1, x2 = sorted([self._sx, e.x])
        y1, y2 = sorted([self._sy, e.y])

        if (x2 - x1) < 40 or (y2 - y1) < 8:
            if self._rect_id:
                self.canvas.delete(self._rect_id)
            return

        # Knip uit de oorspronkelijke volledige screenshot
        screenshot = self._full_shot.crop((x1, y1, x2, y2))
        self.win.destroy()
        self.root.deiconify()
        self.callback(screenshot, (x1, y1, x2, y2))

    def _on_escape(self, _event):
        self.win.destroy()
        self.root.deiconify()
        self.callback(None, None)
