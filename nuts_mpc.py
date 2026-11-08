"""
nuts_mpc.py
===========
Kontrola nakretek MacPherson (FWS1 OP40) - wspolna logika stanowisk 0004 i 0005.
Dozwolona liczba nakretek jest indywidualna dla stanowiska: config.INSPECTIONS[plc_id]["ok_counts"].
"""
from __future__ import annotations

import logging

from inspections.common import InspectionResult, detect, draw_boxes, failed

logger = logging.getLogger("inspections.nuts_mpc")


def run(plc_id: str, image, params: dict) -> InspectionResult:
    if image is None:
        logger.error("[%s] Brak obrazu", plc_id)
        return failed("NOK brak obrazu")

    debug = image.copy()
    detections = detect(params, image)
    draw_boxes(debug, detections, "NUT")

    count = len(detections)
    ok_counts = tuple(params["ok_counts"])
    status = "OK" if count in ok_counts else "NOK"

    logger.info(
        "[%s] Nakretki: %d szt. -> %s (OK dla %s)", plc_id, count, status, ok_counts,
    )
    return InspectionResult(f"{status} Nakretki MPC: {count} szt.", debug, image, status)
