"""
0003_inspect.py - TSV2, skrzynki sekwencyjne. Brak detekcji = OK, kazda detekcja = NOK.
"""
import logging

from inspections.common import (
    InspectionResult, detect, draw_boxes, failed, get_params, warmup as _warmup,
)

PLC_ID = "0003"
logger = logging.getLogger("inspections.0003")


def run_inspection(plc_id: str, image) -> InspectionResult:
    if image is None:
        logger.error("[%s] Brak obrazu", plc_id)
        return failed("NOK brak obrazu")

    params = get_params(PLC_ID)
    debug = image.copy()
    detections = detect(params, image)
    draw_boxes(debug, detections, "BOX")

    count = len(detections)
    if count > 0:
        logger.warning("[%s] Wykryto skrzynki: %d szt.", plc_id, count)
        return InspectionResult(
            f"NOK Skrzynki sekwencyjne wykryto: {count} szt.", debug, image, "NOK"
        )

    logger.info("[%s] OK brak skrzynek", plc_id)
    return InspectionResult("OK brak skrzynek", debug, image, "OK")


def warmup() -> None:
    _warmup(get_params(PLC_ID))
