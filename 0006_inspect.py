"""
0006_inspect.py - wtyczka OBD, 16 pinow (logika: pins_obd.py, parametry: config.INSPECTIONS["0006"]).
"""
from inspections import pins_obd
from inspections.common import InspectionResult, get_params, warmup as _warmup

PLC_ID = "0006"


def run_inspection(plc_id: str, image) -> InspectionResult:
    return pins_obd.run(plc_id, image, get_params(PLC_ID))


def warmup() -> None:
    _warmup(get_params(PLC_ID))
