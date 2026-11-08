"""
0001_inspect.py - F001, odczyt numeru skida z kodu DataMatrix.
"""
import logging

import cv2
from pylibdmtx.pylibdmtx import decode

from inspections.common import InspectionResult, crop_xyxy, failed, get_params

PLC_ID = "0001"
logger = logging.getLogger("inspections.0001")


def _preprocess(roi_gray, scale_percent: int):
    img = cv2.medianBlur(roi_gray, 3)
    img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    width = int(img.shape[1] * scale_percent / 100)
    height = int(img.shape[0] * scale_percent / 100)
    return cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)


def run_inspection(plc_id: str, image) -> InspectionResult:
    if image is None:
        logger.error("[%s] Brak obrazu", plc_id)
        return failed("NOK brak obrazu")

    params = get_params(PLC_ID)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    roi = crop_xyxy(gray, params["roi_xyxy"])
    if roi is None:
        logger.error("[%s] Pusty ROI %s", plc_id, params["roi_xyxy"])
        return failed("NOK brak ROI", image)

    debug = roi.copy()
    results = decode(_preprocess(roi, params["scale_percent"]))

    if results:
        code = results[0].data.decode("utf-8", "replace")
        logger.info("[%s] Odczytany kod: %s", plc_id, code)
        return InspectionResult(f"OK skid nr: {code}", debug, image, f"OK{code}")

    logger.warning("[%s] DataMatrix nie zostal odczytany", plc_id)
    return InspectionResult("NOK brak odczytu", debug, image, "NOK")
