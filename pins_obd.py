"""
pins_obd.py
===========
Kontrola 16 pinow wtyczki OBD - wspolna logika stanowisk 0002 i 0006.

Kryteria:
  * dokladnie expected_pins detekcji (nadmiarowe odrzucane po confidence),
  * podzial na 2 rzedy po globalnej medianie Y, w kazdym expected_pins/2,
  * odchylenie pinu od mediany Y rzedu <= line_tol_px,
  * odchylenie odstepu od mediany odstepow <= spacing_tol_px.
"""
from __future__ import annotations

import logging
import time

import cv2
import numpy as np

from inspections.common import (
    BLUE, GREEN, RED, InspectionResult, crop_xyxy, detect, failed, point,
)

logger = logging.getLogger("inspections.pins_obd")


def _draw_confidences(image: np.ndarray, centers: np.ndarray, conf: np.ndarray) -> None:
    for (cx, cy), value in zip(centers, conf):
        cv2.putText(
            image, f"{value:.2f}", (int(cx), int(cy) - 5),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 1,
        )


def _split_rows(centers: np.ndarray):
    by_x = centers[np.argsort(centers[:, 0])]
    median_y = float(np.median(by_x[:, 1]))
    return by_x[by_x[:, 1] < median_y], by_x[by_x[:, 1] >= median_y], median_y


def _check_line(image, row, row_no, tol, plc_id) -> bool:
    deviations = np.abs(row[:, 1] - np.median(row[:, 1]))
    bad = np.flatnonzero(deviations > tol)
    for i in bad:
        logger.warning(
            "[%s][ROW %d] Pin %d odchylony od linii o %.2f px",
            plc_id, row_no, i + 1, deviations[i],
        )
        cv2.circle(image, point(row[i]), 7, RED, 2)
    return bad.size == 0


def _check_spacing(image, row, row_no, tol, plc_id) -> bool:
    diffs = np.diff(row[:, 0])
    if diffs.size == 0:
        logger.warning("[%s][ROW %d] Za malo pinow do kontroli odstepow", plc_id, row_no)
        return False

    median_diff = float(np.median(diffs))
    deviations = np.abs(diffs - median_diff)
    logger.info(
        "[%s][ROW %d] diff=%s, median=%.2f",
        plc_id, row_no, np.round(diffs, 1), median_diff,
    )

    bad = np.flatnonzero(deviations > tol)
    for i in bad:
        logger.warning(
            "[%s][ROW %d] Odstep %d-%d odbiega o %.2f px",
            plc_id, row_no, i + 1, i + 2, deviations[i],
        )
        cv2.circle(image, point(row[i]), 9, RED, 2)
        cv2.circle(image, point(row[i + 1]), 9, RED, 2)
    return bad.size == 0


def _check_row(image, row, row_no, params, plc_id) -> bool:
    expected = params["expected_pins"] // 2
    if len(row) != expected:
        logger.warning(
            "[%s][ROW %d] Liczba pinow w rzedzie %d zamiast %d",
            plc_id, row_no, len(row), expected,
        )
        return False

    color = GREEN if row_no == 1 else BLUE
    for xy in row:
        cv2.circle(image, point(xy), 5, color, -1)

    line_ok = _check_line(image, row, row_no, params["line_tol_px"], plc_id)
    spacing_ok = _check_spacing(image, row, row_no, params["spacing_tol_px"], plc_id)
    return line_ok and spacing_ok


def run(plc_id: str, image, params: dict) -> InspectionResult:
    started = time.perf_counter()

    if image is None:
        logger.error("[%s] Brak obrazu", plc_id)
        return failed("NOK brak obrazu")

    roi = crop_xyxy(image, params["roi_xyxy"])
    if roi is None:
        logger.error("[%s] Pusty ROI %s", plc_id, params["roi_xyxy"])
        return failed("NOK brak ROI")

    raw = roi.copy()
    debug = roi.copy()          # roi to widok na klatke kamery - nie rysujemy po oryginale
    expected = params["expected_pins"]

    detections = detect(params, roi).top(expected)
    centers = detections.centers()
    _draw_confidences(debug, centers, detections.conf)

    count = len(detections)
    logger.info("[%s] Wykryto %d/%d pinow", plc_id, count, expected)

    if count != expected:
        message = f"NOK wykryto {count}/{expected} pin"
        logger.warning("[%s] %s", plc_id, message)
        return InspectionResult(message, debug, raw, "NOK")

    upper, lower, median_y = _split_rows(centers)
    logger.info(
        "[%s][ROWS] Row1=%d, Row2=%d, median_y=%.2f",
        plc_id, len(upper), len(lower), median_y,
    )
    upper_ok = _check_row(debug, upper, 1, params, plc_id)
    lower_ok = _check_row(debug, lower, 2, params, plc_id)

    message = f"OK wykryto {count}/{expected}" if upper_ok and lower_ok else "NOK rozstaw pin"
    logger.info(
        "[%s] %s (%.0f ms)", plc_id, message, (time.perf_counter() - started) * 1000,
    )
    return InspectionResult(message, debug, raw, message)
