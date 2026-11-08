"""
camera.py
=========
Jeden watek na kamere: utrzymuje polaczenie RTSP i drenuje bufor (grab bez dekodowania).
Klatka dekodowana dopiero na zadanie inspekcji (retrieve).

Odpornosc na zawieszona kamere:
  * timeouty otwarcia i odczytu strumienia (OpenCV >= 4.6),
  * get() czeka na VideoCapture maksymalnie CAM_LOCK_TIMEOUT_S, potem zwraca None,
  * get() wywolywany jest w watku inspekcji, nigdy w watku nasluchu UDP.
"""
import logging
import re
import threading
import time

import cv2

from config import (
    CAM_BACKOFF_MAX,
    CAM_BACKOFF_START,
    CAM_LOCK_TIMEOUT_S,
    CAM_LOOP_SLEEP,
    CAM_OPEN_TIMEOUT_MS,
    CAM_READ_FAIL_LIMIT,
    CAM_READ_TIMEOUT_MS,
    FRAME_MAX_AGE_MS,
    build_rtsp_urls,
)

logger = logging.getLogger("Camera")


def _mask(url: str) -> str:
    """rtsp://user:pass@host -> rtsp://host (nie logujemy danych logowania)."""
    return re.sub(r"(://)[^@]*@", r"\1", url)


def _open_params() -> list:
    params = []
    if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
        params += [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, CAM_OPEN_TIMEOUT_MS]
    if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
        params += [cv2.CAP_PROP_READ_TIMEOUT_MSEC, CAM_READ_TIMEOUT_MS]
    return params


class CameraWorker:
    def __init__(self, plc_id: str, url: str):
        self.plc_id = plc_id
        self.url = url
        self.cap = None
        self.last_ts = 0.0                    # time.monotonic() ostatniego udanego grab()
        self.cap_lock = threading.Lock()      # VideoCapture nie jest thread-safe
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._fail = 0
        self._backoff = CAM_BACKOFF_START
        self._thread = threading.Thread(
            target=self._loop, name=f"cam-{plc_id}", daemon=True
        )

    # ---------- polaczenie ----------
    def _connect(self):
        params = _open_params()
        if params:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params)
        else:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if cap.isOpened():
            return cap
        cap.release()
        return None

    def _reconnect(self) -> bool:
        logger.info("[%s] RTSP connect %s", self.plc_id, _mask(self.url))
        cap = self._connect()
        if cap is None:
            self._stop.wait(self._backoff)
            self._backoff = min(self._backoff * 2, CAM_BACKOFF_MAX)
            return False
        with self.cap_lock:
            self.cap = cap
        self._backoff = CAM_BACKOFF_START
        self._fail = 0
        return True

    def _release_cap(self) -> None:
        self.ready.clear()
        with self.cap_lock:
            if self.cap is not None:
                self.cap.release()
                self.cap = None

    # ---------- petla ----------
    def _grab(self) -> bool:
        with self.cap_lock:
            return self.cap is not None and self.cap.grab()

    def _on_grab_failure(self) -> None:
        self._fail += 1
        if self._fail > CAM_READ_FAIL_LIMIT:
            logger.warning("[%s] %d nieudanych grab() - reconnect", self.plc_id, self._fail)
            self._release_cap()
            self._fail = 0

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.cap is None and not self._reconnect():
                continue
            if self._grab():
                self._fail = 0
                self.last_ts = time.monotonic()
                self.ready.set()
            else:
                self._on_grab_failure()
            time.sleep(CAM_LOOP_SLEEP)

    # ---------- API ----------
    def start(self) -> None:
        self._thread.start()

    def get(self, max_age_ms: float = FRAME_MAX_AGE_MS):
        """Dekoduje najswiezsza klatke. None, gdy klatka za stara lub kamera zajeta/zawieszona."""
        if self.last_ts == 0:
            return None
        age_ms = (time.monotonic() - self.last_ts) * 1000
        logger.info("[%s] Wiek ramki: %.0f ms", self.plc_id, age_ms)
        if age_ms > max_age_ms:
            return None

        if not self.cap_lock.acquire(timeout=CAM_LOCK_TIMEOUT_S):
            logger.warning(
                "[%s] VideoCapture zajety > %.1f s - kamera zawieszona?",
                self.plc_id, CAM_LOCK_TIMEOUT_S,
            )
            return None
        try:
            if self.cap is None:
                return None
            ret, frame = self.cap.retrieve()
        finally:
            self.cap_lock.release()
        return frame if ret else None

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=CAM_READ_TIMEOUT_MS / 1000 + 1)
        self._release_cap()


camera_buffers: dict = {}


def get_camera(plc_id: str):
    return camera_buffers.get(plc_id)


def start_all() -> None:
    """Tworzy i uruchamia workery dla wszystkich kamer z konfiguracji."""
    if camera_buffers:
        return
    for plc_id, url in build_rtsp_urls().items():
        worker = CameraWorker(plc_id, url)
        camera_buffers[plc_id] = worker
        worker.start()
    logger.info("CAMERA WORKERS STARTED (%d kamer)", len(camera_buffers))


def stop_all() -> None:
    for worker in camera_buffers.values():
        worker.stop()
    logger.info("CAMERA WORKERS STOPPED")
