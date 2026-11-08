"""
udp_server.py
=============
Nasluch UDP na triggery PLC (prefiks "CAM").

Watek nasluchu TYLKO waliduje telegram i przekazuje go do puli - nie dotyka kamer
ani modeli, wiec zawieszona kamera nie blokuje triggerow innych stanowisk.
Na kazde stanowisko maksymalnie jedno zadanie w toku (kolejne -> BUSY).
"""
import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import inspection
from config import (
    FRAME_LEN, HOST, MAX_BACKLOG, MAX_INSPECTIONS, PLC_ALLOWED_IPS, PORT, UDP_RECV_TIMEOUT_S,
)

logger = logging.getLogger("UDP")


class UdpServer:
    def __init__(self, stop_event: threading.Event):
        self._stop = stop_event
        self._executor = ThreadPoolExecutor(
            max_workers=MAX_INSPECTIONS, thread_name_prefix="insp"
        )
        self._slots = threading.BoundedSemaphore(MAX_INSPECTIONS + MAX_BACKLOG)
        self._in_flight: set = set()
        self._lock = threading.Lock()

    # ---------- rezerwacja stanowiska ----------
    def _try_reserve(self, plc_id: str) -> bool:
        with self._lock:
            if plc_id in self._in_flight:
                return False
            if not self._slots.acquire(blocking=False):
                return False
            self._in_flight.add(plc_id)
            return True

    def _release(self, plc_id: str) -> None:
        with self._lock:
            self._in_flight.discard(plc_id)
        self._slots.release()

    def _run(self, plc_id, data, addr, sock) -> None:
        try:
            inspection.handle_request(plc_id, data, addr, sock)
        except Exception:
            logger.exception("[%s] Nieobsluzony blad zadania", plc_id)
        finally:
            self._release(plc_id)

    # ---------- walidacja i przekazanie ----------
    def _dispatch(self, sock, data: bytes, addr) -> None:
        if not data.startswith(b"CAM"):
            return
        if PLC_ALLOWED_IPS and addr[0] not in PLC_ALLOWED_IPS:
            logger.warning("Odrzucono telegram z niedozwolonego adresu %s", addr[0])
            return
        if len(data) < FRAME_LEN:
            logger.warning("Za krotki telegram (%d B) od %s", len(data), addr[0])
            return

        plc_id = data[3:7].decode("ascii", "replace")
        if not inspection.is_known(plc_id):
            logger.warning("Nieznane plc_id %r od %s", plc_id, addr[0])
            sock.sendto(b"NO_IMAGE", addr)
            return

        if not self._try_reserve(plc_id):
            logger.warning("[%s] BUSY - poprzednie zadanie stanowiska w toku", plc_id)
            sock.sendto(b"BUSY", addr)
            return

        try:
            self._executor.submit(self._run, plc_id, data, addr, sock)
        except RuntimeError:            # executor zamkniety (shutdown)
            self._release(plc_id)

    # ---------- petla ----------
    def serve_forever(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((HOST, PORT))
            sock.settimeout(UDP_RECV_TIMEOUT_S)
            logger.info("UDP listening %s:%d", HOST, PORT)
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(1024)
                except socket.timeout:
                    continue
                except ConnectionResetError:
                    # Windows: ICMP "port unreachable" po wczesniejszym sendto - ignorujemy
                    continue
                try:
                    self._dispatch(sock, data, addr)
                except OSError:
                    logger.exception("UDP DISPATCH ERROR")
        finally:
            sock.close()

    def close(self) -> None:
        """Zatrzymuje przyjmowanie zadan i czeka na zakonczenie trwajacych inspekcji."""
        self._stop.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        logger.info("UDP server zatrzymany")
