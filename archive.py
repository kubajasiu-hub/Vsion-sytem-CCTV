"""
archive.py
==========
Zapis obrazow (JPEG, jakosc z config.JPEG_QUALITY = 100) + przekazanie rekordow do bazy.
Osobny watek - zapis na dysk nigdy nie opoznia odpowiedzi do PLC.
"""
import logging
import os
import threading
from queue import Queue

import cv2

import database
from config import ARCHIVE_QUEUE_MAXSIZE, IMAGES_ROOT, JPEG_QUALITY

logger = logging.getLogger("Archive")

archive_queue: "Queue" = Queue(maxsize=ARCHIVE_QUEUE_MAXSIZE)
_STOP = object()
_archive_thread = None


def _db_image_path(day: str, plc_id: str, name: str) -> str:
    """
    Sciezka zapisywana w bazie - format bez zmian wzgledem poprzedniej wersji:
    "08_archive/<dzien>/<plc_id>/<plik>.jpg" (wzgledna; serwer WS mapuje ja na /static).
    """
    return os.path.join(IMAGES_ROOT.name, day, plc_id, name)


def save_image(date, plc_id: str, frame, res: str):
    """Zapisuje klatke JPEG. Zwraca sciezke dla bazy albo None, gdy brak obrazu."""
    if frame is None:
        return None
    day = date.strftime("%Y-%m-%d")
    dir_path = IMAGES_ROOT / day / plc_id
    dir_path.mkdir(parents=True, exist_ok=True)

    name = f"{date.strftime('%H%M%S_%f')}_{plc_id}_{res}.jpg"
    if not cv2.imwrite(str(dir_path / name), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
        raise OSError(f"cv2.imwrite nie zapisal pliku {dir_path / name}")
    return _db_image_path(day, plc_id, name)


def _process(item) -> None:
    (
        date, plc_id, result, frame, raw_frame,
        pin, body_type, body_code, aux,
        duration,
    ) = item
    res = result[:2]          # "OK" / "NO" - sufiks nazwy pliku, uzywany przez cleanup.py
    image_path = None
    try:
        image_path = save_image(date, plc_id, frame, res)
        save_image(date, plc_id, raw_frame, res + "_RAW")
    except Exception:
        logger.exception("[%s] IMAGE SAVE ERROR", plc_id)

    database.save_status(plc_id, result, image_path)
    database.save_inspection(
        plc_id, date.isoformat(), pin, body_type, body_code, aux,
        result, image_path, duration,
    )


def _archive_worker() -> None:
    logger.info("ARCHIVE WORKER STARTED -> %s", IMAGES_ROOT)
    while True:
        item = archive_queue.get()
        if item is _STOP:
            break
        try:
            _process(item)
        except Exception:
            logger.exception("ARCHIVE WORKER ERROR")
    logger.info("ARCHIVE WORKER STOPPED")


def start() -> None:
    global _archive_thread
    if _archive_thread and _archive_thread.is_alive():
        return
    _archive_thread = threading.Thread(target=_archive_worker, daemon=True, name="archive")
    _archive_thread.start()


def stop(timeout: float = 30.0) -> None:
    """Dokancza zapis wszystkiego, co jest w kolejce, i czeka na watek."""
    if not (_archive_thread and _archive_thread.is_alive()):
        return
    archive_queue.put(_STOP)
    _archive_thread.join(timeout=timeout)
    if _archive_thread.is_alive():
        logger.warning("Watek archiwum nie zakonczyl sie w %.0f s", timeout)
