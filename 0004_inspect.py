"""
0004_inspect.py - nakretki MacPherson (logika: nuts_mpc.py, parametry i ok_counts: config.INSPECTIONS["0004"]).
"""
from inspections import nuts_mpc
from inspections.common import InspectionResult, get_params, warmup as _warmup

PLC_ID = "0004"


def run_inspection(plc_id: str, image) -> InspectionResult:
    return nuts_mpc.run(plc_id, image, get_params(PLC_ID))


def warmup() -> None:
    _warmup(get_params(PLC_ID))
