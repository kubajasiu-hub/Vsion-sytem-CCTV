"""
main.py
=======
Start:  logi -> baza -> kamery -> archiwum -> zaladowanie i rozgrzanie modeli -> cleanup -> UDP
Stop (Ctrl+C / SIGTERM z kontenera):
        UDP (czeka na trwajace inspekcje) -> kamery -> archiwum (dopisuje kolejke) -> baza
"""
import logging
import signal
import threading

import archive
import camera
import cleanup
import database
import inspection
from log_setup import setup_logging
from udp_server import UdpServer

_stop_event = threading.Event()


def _handle_signal(signum, _frame) -> None:
    logging.info("Otrzymano sygnal %s - zatrzymywanie", signum)
    _stop_event.set()


def _install_signal_handlers() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    if hasattr(signal, "SIGBREAK"):            # Windows: Ctrl+Break / zamkniecie konsoli
        signal.signal(signal.SIGBREAK, _handle_signal)


def shutdown(server: UdpServer) -> None:
    logging.info("Zamykanie backendu...")
    server.close()
    camera.stop_all()
    archive.stop(timeout=30)
    database.stop(timeout=10)
    logging.info("Backend zamkniety.")


def main() -> None:
    setup_logging()
    _install_signal_handlers()

    database.start()
    camera.start_all()              # kamery lacza sie rownolegle z ladowaniem modeli
    archive.start()
    inspection.warmup_all()         # modele: jeden raz + rozgrzanie, PRZED pierwszym triggerem
    cleanup.start_cleanup_thread()

    server = UdpServer(stop_event=_stop_event)
    try:
        while not _stop_event.is_set():
            try:
                server.serve_forever()
            except Exception:
                logging.exception("UDP SERVER ERROR - ponowny start za 5 s")
                _stop_event.wait(5)
    finally:
        shutdown(server)


if __name__ == "__main__":
    main()
