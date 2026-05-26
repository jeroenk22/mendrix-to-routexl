"""
config.py
Sla RouteXL-inloggegevens op en lees ze terug.
- Gebruikersnaam: ~/.mendrix_routexl/config.json (plaintext, niet gevoelig)
- Wachtwoord:     Windows Credential Manager via keyring (versleuteld)
"""

import json
import os

CONFIG_DIR  = os.path.join(os.path.expanduser("~"), ".mendrix_routexl")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
KEYRING_SERVICE = "MendrixRouteXL"
KEYRING_USER    = "routexl_password"


def _keyring():
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def load_credentials() -> tuple[str, str]:
    """Geeft (username, password) terug; lege strings als niet opgeslagen."""
    username = ""
    password = ""

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            username = data.get("username", "")
        except Exception:
            pass

    kr = _keyring()
    if kr and username:
        try:
            stored = kr.get_password(KEYRING_SERVICE, username)
            if stored:
                password = stored
        except Exception:
            pass

    return username, password


def save_credentials(username: str, password: str) -> None:
    """Sla username op in config-bestand, password in Credential Manager."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"username": username}, f)
    except Exception as e:
        print(f"[Config] Kon gebruikersnaam niet opslaan: {e}", flush=True)

    kr = _keyring()
    if kr:
        try:
            kr.set_password(KEYRING_SERVICE, username, password)
        except Exception as e:
            print(f"[Config] Kon wachtwoord niet opslaan in Credential Manager: {e}", flush=True)
    else:
        print("[Config] keyring niet beschikbaar – wachtwoord niet opgeslagen.", flush=True)
