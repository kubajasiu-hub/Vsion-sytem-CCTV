"""
config.py
=========
Centralna konfiguracja backendu.

Zasady:
  * brak sciezek absolutnych w kodzie - wszystko liczone wzgledem BASE_DIR,
  * kazda sciezka/port/limit moze byc nadpisana zmienna srodowiskowa
    (lokalnie: Windows, docelowo: kontener / OpenShift ConfigMap + Secret),
  * import tego modulu NIE wymaga danych RTSP - sa potrzebne dopiero
    w build_rtsp_urls() (dzieki temu cleanup.py i narzedzia dzialaja samodzielnie).
"""
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote


# ===================== KATALOGI BAZOWE =====================
BASE_DIR = Path(__file__).resolve().parent      # .../Projects/02_backend
PROJECT_DIR = BASE_DIR.parent                   # .../Projects


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).resolve() if value else default.resolve()


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


# ===================== SCIEZKI =====================
DB_FILE = _env_path("VISION_DB_FILE", BASE_DIR / "db" / "plc.db")
IMAGES_ROOT = _env_path("VISION_STATIC_DIR", PROJECT_DIR / "08_archive")
MODELS_DIR = _env_path("VISION_MODELS_DIR", BASE_DIR / "inspections" / "models")
LOG_FILE = _env_path("VISION_LOG_FILE", BASE_DIR / "logs" / "backend.log")

# ===================== SIEC (UDP <-> PLC) =====================
HOST = os.environ.get("VISION_UDP_HOST", "0.0.0.0")
PORT = _env_int("VISION_UDP_PORT", 5155)
UDP_RECV_TIMEOUT_S = 1.0          # co ile petla UDP sprawdza sygnal zatrzymania

# Lista dozwolonych adresow PLC, np. "10.25.1.10,10.25.1.11". Pusta = brak filtra.
PLC_ALLOWED_IPS = frozenset(
    ip.strip()
    for ip in os.environ.get("PLC_ALLOWED_IPS", "").split(",")
    if ip.strip()
)

# ===================== TELEGRAM PLC =====================
FRAME_LEN = 74                    # dlugosc telegramu (bajty)
AUX_START = 32
AUX_LEN = 17                      # pole AUX: dokladnie 17 BAJTOW ASCII

# ===================== LOGI =====================
LOG_LEVEL = logging.INFO
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB na plik
LOG_BACKUP_COUNT = 10

# ===================== LIMITY =====================
MAX_INSPECTIONS = _env_int("VISION_MAX_INSPECTIONS", 10)   # watki inspekcji
MAX_BACKLOG = _env_int("VISION_MAX_BACKLOG", 20)           # zadania czekajace w kolejce
ARCHIVE_QUEUE_MAXSIZE = _env_int("VISION_ARCHIVE_QUEUE", 100)  # kazdy element = 2 obrazy w RAM
JPEG_QUALITY = 100

# ===================== KAMERY =====================
FRAME_MAX_AGE_MS = 3000           # maksymalny wiek ramki uznawanej za swieza
CAM_READ_FAIL_LIMIT = 5           # nieudane grab() przed reconnectem
CAM_LOOP_SLEEP = 0.005            # mikro-sleep w petli grab
CAM_BACKOFF_START = 0.5           # [s]
CAM_BACKOFF_MAX = 5.0             # [s]
CAM_OPEN_TIMEOUT_MS = 5000        # timeout otwarcia strumienia (OpenCV >= 4.6)
CAM_READ_TIMEOUT_MS = 5000        # timeout grab() - zawieszony strumien nie wisi w nieskonczonosc
CAM_READY_WAIT_S = 0.5            # ile czekac na pierwsza klatke kamery
CAM_LOCK_TIMEOUT_S = 1.0          # ile czekac na dostep do VideoCapture przy pobraniu klatki

# Rejestratory Dahua: plc_id -> (ip, channel, subtype).
# Nadpisanie bez zmiany kodu: VISION_CAMERAS_JSON='{"0001": {"ip": "...", "channel": 12, "subtype": 1}}'
DEFAULT_CAMERAS = {
    "0001": ("10.25.238.16", 12, 1),
    "0002": ("10.25.238.12", 18, 0),
    "0003": ("10.25.238.15", 18, 1),
    "0004": ("10.25.234.78", 9, 1),
    "0005": ("10.25.234.78", 8, 1),
    "0006": ("10.25.238.12", 31, 0),
}


def _load_cameras() -> dict:
    raw = os.environ.get("VISION_CAMERAS_JSON")
    if not raw:
        return dict(DEFAULT_CAMERAS)
    data = json.loads(raw)
    return {
        plc_id: (cam["ip"], int(cam["channel"]), int(cam["subtype"]))
        for plc_id, cam in data.items()
    }


CAMERAS = _load_cameras()


def build_rtsp_urls() -> dict:
    """URL-e RTSP z danymi logowania ze zmiennych srodowiskowych RTSP_USER / RTSP_PASS."""
    user = os.environ.get("RTSP_USER")
    password = os.environ.get("RTSP_PASS")
    if not user or not password:
        raise RuntimeError("Brak zmiennych srodowiskowych RTSP_USER / RTSP_PASS.")

    user_q = quote(user, safe="")
    pass_q = quote(password, safe="")
    return {
        plc_id: (
            f"rtsp://{user_q}:{pass_q}@{ip}:554/cam/realmonitor"
            f"?channel={channel}&subtype={subtype}"
        )
        for plc_id, (ip, channel, subtype) in CAMERAS.items()
    }


# ===================== INSPEKCJE =====================
# Jedno zrodlo prawdy dla parametrow stanowisk.
#   model      - nazwa pliku w MODELS_DIR (ten sam plik = jedna instancja w pamieci)
#   backend    - "ultralytics" | "libreyolo"
#   conf/iou/imgsz - parametry predykcji (pomijane, jesli brak)
#   roi_xyxy   - (x1, y1, x2, y2) w pikselach pelnej klatki
#   ok_counts  - liczby detekcji uznawane za OK
INSPECTIONS = {
    "0001": {   # F001 - numer skida, DataMatrix
        "roi_xyxy": (210, 80, 510, 380),
        "scale_percent": 80,
    },
    "0002": {   # FA45 - wtyczka OBD, 16 pinow
        "model": "0002_bestOBD.pt",
        "backend": "ultralytics",
        "conf": 0.85,
        "iou": 0.30,
        "roi_xyxy": (700, 140, 1340, 780),
        "expected_pins": 16,
        "line_tol_px": 8.0,
        "spacing_tol_px": 8.0,
    },
    "0003": {   # TSV2 - skrzynki sekwencyjne (brak detekcji = OK)
        "model": "bestTSV2.pt",
        "backend": "ultralytics",
        "conf": 0.80,
    },
    "0004": {   # FWS1 OP40R - nakretki, prawa strona
        "model": "bestMPC_LY9m.pt",
        "backend": "libreyolo",
        "conf": 0.85,
        "imgsz": 1024,
        "ok_counts": (3,),
    },
    "0005": {   # FWS1 OP40L - nakretki, lewa strona (czesc moze byc zasloniecia wiazka)
        "model": "bestMPC_LY9m.pt",
        "backend": "libreyolo",
        "conf": 0.70,
        "iou": 0.45,
        "imgsz": 1024,
        "ok_counts": (2, 3),
    },
    "0006": {   # wtyczka OBD - ta sama logika co 0002
        "model": "0002_bestOBD.pt",
        "backend": "ultralytics",
        "conf": 0.70,
        "iou": 0.30,
        "roi_xyxy": (700, 140, 1340, 780),
        "expected_pins": 16,
        "line_tol_px": 8.0,
        "spacing_tol_px": 8.0,
    },
}
