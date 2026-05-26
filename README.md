# Mendrix → RouteXL

Desktop-app (Windows) die rijen op het Ritten-scherm van [Mendrix](https://mendrix.nl/) uitleest via OCR en de adressen als geoptimaliseerde route doorstuurt naar [RouteXL](https://www.routexl.nl/).

---

## Werking in het kort

1. Detecteert dat [Mendrix](https://mendrix.nl/) open is en op het Ritten-scherm staat.
2. Je tekent een rechthoek over de tabelrijen die je wilt plannen.
3. Een screenshot van die regio wordt via Tesseract OCR ingelezen en omgezet naar adressen.
4. De adressen worden geocodeerd (lat/lon) — Nederlandse postcodes via PDOK, overige via Nominatim.
5. Een controlescherm laat je stops aan/uitzetten, handelingstijden aanpassen en start-/eindadres corrigeren.
6. De route wordt verstuurd naar de [RouteXL](https://www.routexl.nl/) API en het geoptimaliseerde resultaat wordt getoond, inclusief kaartweergave.

---

## Vereisten

| # | Vereiste | Link |
|---|---|---|
| 1 | **Python 3.11 of hoger** | https://www.python.org/downloads/ |
| 2 | **Tesseract OCR** (Windows-installer van UB Mannheim) | https://github.com/UB-Mannheim/tesseract/wiki |
| 3 | **[RouteXL](https://www.routexl.nl/)-account** | https://www.routexl.nl/ |

> **Tesseract taalpakketten:** kies tijdens de installatie voor _Additional language data_ en vink **Nederlands (nld)** en **Duits (deu)** aan.

---

## Installatie

```bat
pip install -r requirements.txt
```

---

## Starten

```bat
python main.py
```

Of maak een `start.bat` aan in de projectmap:

```bat
@echo off
cd /d "%~dp0"
python main.py
pause
```

Dubbelklik dan op `start.bat` om de app te starten zonder een terminal open te laten.

---

## Gebruik

1. Open [Mendrix](https://mendrix.nl/) en ga naar het **Ritten-scherm**.
2. Start de app (`python main.py`). Het groene bolletje in het hoofdvenster verschijnt zodra [Mendrix](https://mendrix.nl/) herkend wordt.
3. Klik **Start Selectie** en teken een rechthoek om de gewenste rijen — inclusief de rij met het Startadres als je tijdvensters wilt meegeven.
4. Het controlescherm verschijnt:
   - Pas de **Starttijd** aan indien nodig.
   - Vink stops aan of uit met de checkboxes.
   - Wijzig de **Handelingstijd** per stop (standaard 5 min).
   - Ontbreekt het start- of eindadres? Gebruik de **+ Invoeren**-knop.
5. **[RouteXL](https://www.routexl.nl/)-inloggegevens invoeren** (zie sectie hieronder).
6. De knop **Doorzetten naar [RouteXL](https://www.routexl.nl/)** wordt groen zodra alle adressen geocodeerd zijn — klik hem om de route te versturen.
7. Het resultaatscherm toont de geoptimaliseerde volgorde met aankomsttijden en een interactieve kaart.

---

## RouteXL-inloggegevens instellen

De app heeft een account bij [RouteXL](https://www.routexl.nl/) nodig om routes te berekenen.

### Eenmalig instellen

1. Maak een account aan op [routexl.nl](https://www.routexl.nl/) als je er nog geen hebt.
2. Start de app en vul in het controlescherm (onderaan) je **Gebruikersnaam** en **Wachtwoord** in.  
   > Let op: vul je **gebruikersnaam** in, niet je e-mailadres.
3. Klik **Doorzetten naar RouteXL** — de app slaat de gegevens daarna automatisch op:
   - **Gebruikersnaam** → `~/.mendrix_routexl/config.json` (plaintext, niet gevoelig)
   - **Wachtwoord** → Windows Credential Manager (versleuteld via `keyring`)

De volgende keer worden de gegevens automatisch ingevuld.

### Credentials verwijderen

- Verwijder `%USERPROFILE%\.mendrix_routexl\config.json` voor de gebruikersnaam.
- Open **Referentiebeheer** (Credential Manager) in Windows en verwijder de vermelding `MendrixRouteXL` voor het wachtwoord.

---

## Geocodering

| Adrestype | Service |
|---|---|
| Nederlandse postcodes (`1234 AB`) | [PDOK Locatieserver](https://api.pdok.nl/) (Kadaster, gratis, snel) |
| Overige / Duits | [Nominatim / OpenStreetMap](https://nominatim.openstreetmap.org/) (gratis, max. 1 req/sec) |

Adressen die niet geocodeerd konden worden, worden gemarkeerd. Je kunt ze overslaan of handmatig corrigeren in het controlescherm.

---

## Limieten RouteXL gratis plan

Het gratis [RouteXL](https://www.routexl.nl/)-plan ondersteunt maximaal **10 locaties** per route (inclusief start en eind). Upgrade naar een betaald plan voor meer stops.

---

## Pakketteren als .exe (optioneel)

```bat
pip install pyinstaller
pyinstaller --onefile --windowed --name MendrixRouteXL main.py
```

De `.exe` staat daarna in de `dist/`-map. Houd er rekening mee dat Tesseract apart geïnstalleerd moet blijven, of gebruik `--add-data` om de Tesseract-data mee te bundelen.

---

## Veelgestelde problemen

| Symptoom | Oplossing |
|---|---|
| _"Tesseract niet gevonden"_ | Installeer Tesseract via de link bij Vereisten en herstart de app |
| Adressen worden niet herkend | Zorg dat de selectie de kolommen Straat, Postcode én Plaats omvat |
| Geocodering mislukt | Controleer je internetverbinding |
| HTTP 401 van [RouteXL](https://www.routexl.nl/) | Verkeerde gebruikersnaam of wachtwoord (gebruik géén e-mailadres) |
| HTTP 403 van [RouteXL](https://www.routexl.nl/) | Te veel stops voor je abonnement (gratis = max. 10) |
| HTTP 429 van [RouteXL](https://www.routexl.nl/) | Er loopt al een berekening — wacht even en probeer opnieuw |

---

## Technische stack

- **Python 3.11+** met `tkinter` voor de GUI
- **Tesseract OCR** via `pytesseract`
- **OpenCV** voor beeldpreprocessing (tabellijnen verwijderen, binariseren)
- **Pillow** voor screenshots (`ImageGrab`)
- **pywin32** voor Mendrix-vensterdetectie
- **PDOK** + **Nominatim** voor geocodering
- **[RouteXL](https://www.routexl.nl/) API** (`POST /tour/`) voor routeoptimalisatie
