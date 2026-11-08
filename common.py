"""
common.py
=========
Wspolny kontrakt i pomocnicze funkcje modulow inspekcji.

Kazdy modul inspections/<plc_id>_inspect.py eksportuje:
    run_inspection(plc_id, image) -> InspectionResult   (zawsze 4 pola)
    warmup()                                             (opcjonalnie)
"""
from __future__ import annotations

from typing import NamedTuple, Optional

import cv2
import numpy as np

from config import INSPECTIONS
from inspections import model_registry

GREEN = (0, 255, 0)
BLUE = (255, 0, 0)
RED = (0, 0, 255)

_PREDICT_KEYS = ("conf", "iou", "imgsz")


class InspectionResult(NamedTuple):
    result: str                       # tekst wyniku (dashboard / baza), zaczyna sie od OK / NOK
    debug_frame: Optional[np.ndarray] # obraz z naniesionymi wynikami
    raw_frame: Optional[np.ndarray]   # obraz bez adnotacji
    aux: str                          # tekst do pola AUX telegramu PLC


def failed(message: str, raw_frame: Optional[np.ndarray] = None) -> InspectionResult:
    return InspectionResult(message, None, raw_frame, "NOK")


def get_params(plc_id: str) -> dict:
    return INSPECTIONS[plc_id]


def predict_kwargs(params: dict) -> dict:
    return {key: params[key] for key in _PREDICT_KEYS if key in params}


def crop_xyxy(image: np.ndarray, roi_xyxy: tuple) -> Optional[np.ndarray]:
    x1, y1, x2, y2 = roi_xyxy
    roi = image[y1:y2, x1:x2]
    return roi if roi.size else None


def detect(params: dict, image: np.ndarray) -> model_registry.Detections:
    return model_registry.detect(
        params["model"], params["backend"], image, **predict_kwargs(params)
    )


def warmup(params: dict, shape: Optional[tuple] = None) -> None:
    """Rozgrzewa model stanowiska. Domyslny rozmiar: ROI albo imgsz x imgsz."""
    if shape is None:
        if "roi_xyxy" in params:
            x1, y1, x2, y2 = params["roi_xyxy"]
            shape = (y2 - y1, x2 - x1, 3)
        else:
            size = params.get("imgsz", 640)
            shape = (size, size, 3)
    model_registry.warmup(
        params["model"], params["backend"], shape, **predict_kwargs(params)
    )


def point(xy) -> tuple:
    return int(xy[0]), int(xy[1])


def draw_boxes(image: np.ndarray, detections: model_registry.Detections, label: str) -> None:
    for (x1, y1, x2, y2), conf in zip(detections.xyxy, detections.conf):
        cv2.rectangle(image, (int(x1), int(y1)), (int(x2), int(y2)), GREEN, 2)
        cv2.putText(
            image, f"{label} {conf:.2f}", (int(x1), int(y1) - 5),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, GREEN, 2,
        )
