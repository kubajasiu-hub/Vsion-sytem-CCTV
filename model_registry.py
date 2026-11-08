"""
model_registry.py
=================
Wspolny rejestr modeli detekcyjnych.

  * kazdy plik modelu ladowany JEDEN raz (0004 i 0005 dziela ta sama instancje),
  * kazdy model ma wlasna blokade - rownolegle predict() na tym samym obiekcie
    nie sa wykonywane; rozne modele nie blokuja sie nawzajem,
  * wynik ujednolicony do tablic NumPy niezaleznie od biblioteki
    (Ultralytics / LibreYOLO).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

from config import MODELS_DIR

logger = logging.getLogger("ModelRegistry")

_entries: dict = {}
_warmed: set = set()
_registry_lock = threading.Lock()


# ===================== WYNIK DETEKCJI =====================
@dataclass(frozen=True)
class Detections:
    xyxy: np.ndarray   # (N, 4) float32
    conf: np.ndarray   # (N,)   float32

    def __len__(self) -> int:
        return int(self.conf.shape[0])

    def top(self, k: int) -> "Detections":
        """K detekcji o najwyzszym confidence."""
        if len(self) <= k:
            return self
        idx = np.argsort(-self.conf)[:k]
        return Detections(self.xyxy[idx], self.conf[idx])

    def centers(self) -> np.ndarray:
        """Srodki boxow (N, 2)."""
        if len(self) == 0:
            return np.empty((0, 2), dtype=np.float32)
        x1, y1, x2, y2 = self.xyxy.T
        return np.column_stack(((x1 + x2) / 2, (y1 + y2) / 2)).astype(np.float32)


_EMPTY = Detections(np.empty((0, 4), np.float32), np.empty((0,), np.float32))


def _to_numpy(value) -> np.ndarray:
    if isinstance(value, np.ndarray):
        return value
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        return value.numpy()
    if hasattr(value, "tolist"):
        return np.asarray(value.tolist())
    return np.asarray(value)


def _to_detections(result) -> Detections:
    boxes = getattr(result, "boxes", None)
    if boxes is None or getattr(boxes, "xyxy", None) is None:
        return _EMPTY
    xyxy = _to_numpy(boxes.xyxy).reshape(-1, 4).astype(np.float32)
    conf = _to_numpy(boxes.conf).reshape(-1).astype(np.float32)
    return Detections(xyxy, conf)


# ===================== LADOWANIE =====================
def _resolve(name: str):
    models_dir = MODELS_DIR.resolve()
    path = (models_dir / name).resolve()
    if models_dir not in path.parents:
        raise ValueError(f"Model spoza katalogu modeli: {name}")
    if not path.is_file():
        raise FileNotFoundError(f"Nie znaleziono modelu: {path}")
    return path


def _load(backend: str, path) -> object:
    if backend == "ultralytics":
        from ultralytics import YOLO
        return YOLO(str(path))
    if backend == "libreyolo":
        from libreyolo import LibreYOLO
        return LibreYOLO(str(path))
    raise ValueError(f"Nieznany backend modelu: {backend}")


def get_model(name: str, backend: str):
    """Zwraca (model, blokada). Model ladowany tylko przy pierwszym wywolaniu."""
    key = (backend, name)
    with _registry_lock:
        entry = _entries.get(key)
        if entry is None:
            path = _resolve(name)
            started = time.perf_counter()
            entry = (_load(backend, path), threading.Lock())
            _entries[key] = entry
            logger.info(
                "Zaladowano model %s (%s) w %.0f ms",
                name, backend, (time.perf_counter() - started) * 1000,
            )
    return entry


# ===================== PREDYKCJA =====================
def detect(name: str, backend: str, image: np.ndarray, **predict_kwargs) -> Detections:
    model, lock = get_model(name, backend)
    with lock:
        if backend == "ultralytics":
            result = model.predict(source=image, verbose=False, **predict_kwargs)[0]
        else:
            result = model(image, **predict_kwargs)
            if isinstance(result, (list, tuple)):
                result = result[0]
        return _to_detections(result)


def warmup(name: str, backend: str, shape: tuple, **predict_kwargs) -> None:
    """Pierwsza (wolna) predykcja na pustym obrazie - raz na kombinacje model+rozmiar."""
    key = (backend, name, tuple(shape))
    with _registry_lock:
        if key in _warmed:
            return
    started = time.perf_counter()
    detect(name, backend, np.zeros(shape, dtype=np.uint8), **predict_kwargs)
    with _registry_lock:
        _warmed.add(key)
    logger.info(
        "Rozgrzano model %s %s w %.0f ms",
        name, tuple(shape), (time.perf_counter() - started) * 1000,
    )
