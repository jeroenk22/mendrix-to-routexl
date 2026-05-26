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
    data: dict = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["username"] = username
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
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


def load_tomtom_key() -> str:
    """Geeft TomTom API-sleutel terug uit config-bestand; lege string als niet opgeslagen."""
    if not os.path.exists(CONFIG_FILE):
        return ""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f).get("tomtom_key", "")
    except Exception:
        return ""


def save_tomtom_key(key: str) -> None:
    """Sla TomTom API-sleutel op in config-bestand naast de andere instellingen."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    data: dict = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["tomtom_key"] = key
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[Config] Kon TomTom-sleutel niet opslaan: {e}", flush=True)
