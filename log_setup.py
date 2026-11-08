"""
log_setup.py
============
Root logger: konsola + plik z rotacja. Wywolywane raz z main.py.
"""
import logging
from logging.handlers import RotatingFileHandler

from config import LOG_BACKUP_COUNT, LOG_FILE, LOG_LEVEL, LOG_MAX_BYTES

_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s %(name)s %(message)s")

    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)

    # kopia listy jest celowa: usuwamy elementy w trakcie iteracji (Sonar S7504 - false positive)
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler()
    console.setLevel(LOG_LEVEL)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
    )
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.info("LOGGING STARTED -> %s", LOG_FILE)
