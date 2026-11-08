"""
inspection.py
=============
Obsluga jednego triggera PLC w watku puli:

    pobranie klatki -> inspekcja -> ODPOWIEDZ UDP (priorytet, dokladnie jedna)
                    -> kolejka archiwum (zdjecia + baza, osobny watek)
"""
import importlib
import logging
import threading
import time
from datetime import datetime
from queue import Full

import archive
import camera
from config import AUX_LEN, AUX_START, CAM_READY_WAIT_S, FRAME_LEN, INSPECTIONS

logger = logging.getLogger("Inspection")

_modules: dict = {}
_module_lock = threading.Lock()

# polskie znaki -> ASCII (pole AUX liczone w BAJTACH)
_PL_TO_ASCII = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


# ===================== MODULY =====================
def is_known(plc_id: str) -> bool:
    return plc_id in INSPECTIONS


def get_module(plc_id: str):
    """Import modulu inspekcji - tylko dla plc_id z konfiguracji (whitelist)."""
    if not is_known(plc_id):
        raise KeyError(f"Nieznane plc_id: {plc_id!r}")
    with _module_lock:
        module = _modules.get(plc_id)
        if module is None:
            module = importlib.import_module(f"inspections.{plc_id}_inspect")
            _modules[plc_id] = module
        return module


def warmup_all() -> None:
    """Import modulow + zaladowanie i rozgrzanie modeli PRZED startem nasluchu UDP."""
    for plc_id in INSPECTIONS:
        started = time.perf_counter()
        try:
            module = get_module(plc_id)
            warmup = getattr(module, "warmup", None)
            if callable(warmup):
                warmup()
            logger.info(
                "[%s] Gotowe do pracy (%.0f ms)", plc_id, (time.perf_counter() - started) * 1000
            )
        except Exception:
            logger.exception("[%s] Przygotowanie nieudane - stanowisko bedzie odpowiadac ERROR", plc_id)


# ===================== TELEGRAM =====================
def _aux_bytes(text) -> bytes:
    raw = str(text or "").translate(_PL_TO_ASCII).encode("ascii", "replace")
    return raw[:AUX_LEN].ljust(AUX_LEN, b"-")


def _build_reply(data: bytes, aux_text) -> bytes:
    out = bytearray(data[:FRAME_LEN])
    out[AUX_START:AUX_START + AUX_LEN] = _aux_bytes(aux_text)
    return bytes(out)


def _field(data: bytes, start: int, end: int) -> str:
    return data[start:end].decode("ascii", "replace")


def _send(sock, addr, payload: bytes, plc_id: str) -> None:
    try:
        sock.sendto(payload, addr)
    except OSError:
        logger.exception("[%s] UDP SEND ERROR", plc_id)


# ===================== KROKI =====================
def _get_frame(plc_id: str):
    """(klatka, None) albo (None, kod_bledu_dla_PLC)."""
    cam = camera.get_camera(plc_id)
    if cam is None or not cam.ready.wait(CAM_READY_WAIT_S):
        return None, b"NO_IMAGE"
    frame = cam.get()
    if frame is None:
        return None, b"NO_FRESH_FRAME"
    return frame, None


def _enqueue_archive(plc_id, data, inspection_result, duration_ms) -> None:
    result, debug_frame, raw_frame, _aux = inspection_result
    try:
        archive.archive_queue.put_nowait((
            datetime.now(),
            plc_id,
            result,
            debug_frame,
            raw_frame,
            _field(data, 8, 22),    # pin
            _field(data, 22, 26),   # body_type
            _field(data, 26, 32),   # body_code
            _field(data, 32, 49),   # aux z PLC
            duration_ms,
        ))
    except Full:
        logger.error("[%s] ARCHIVE QUEUE FULL - pomijam zapis", plc_id)


# ===================== GLOWNA FUNKCJA =====================
def handle_request(plc_id: str, data: bytes, addr, sock) -> None:
    started = time.perf_counter()

    frame, error = _get_frame(plc_id)
    if error is not None:
        logger.warning("[%s] %s", plc_id, error.decode())
        _send(sock, addr, error, plc_id)
        return

    inspection_started = time.perf_counter()
    try:
        inspection_result = get_module(plc_id).run_inspection(plc_id, frame)
        result, _debug, _raw, aux_out = inspection_result
    except Exception:
        logger.exception("[%s] INSPECTION ERROR", plc_id)
        _send(sock, addr, b"ERROR", plc_id)
        return
    duration_ms = int((time.perf_counter() - inspection_started) * 1000)

    # PRIORYTET: odpowiedz do PLC - po tym punkcie nic nie wysyla drugiej odpowiedzi
    _send(sock, addr, _build_reply(data, aux_out), plc_id)
    total_ms = (time.perf_counter() - started) * 1000

    _enqueue_archive(plc_id, data, inspection_result, duration_ms)
    logger.info(
        "[%s] %s | inspekcja %d ms | trigger->odpowiedz %.0f ms",
        plc_id, result, duration_ms, total_ms,
    )
