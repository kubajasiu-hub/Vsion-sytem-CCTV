"""
database.py
===========
Jeden watek zapisujacy do SQLite (WAL), zapisy grupowane w paczki.
"""
import logging
import sqlite3
import threading
from datetime import datetime
from queue import Empty, Full, Queue

from config import DB_FILE

# ===================== PARAMETRY =====================
DB_TIMEOUT = 30          # s; musi przetrwac VACUUM/checkpoint z cleanup.py
QUEUE_MAXSIZE = 10000
BATCH_MAX = 200
GET_TIMEOUT = 1.0

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_inspections_plc_ts ON inspections(plc_id, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_inspections_ts ON inspections(ts)",
    "CREATE INDEX IF NOT EXISTS idx_inspections_plc_result_ts ON inspections(plc_id, result, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_plc_status_ts ON plc_status(timestamp)",
)

_STOP = (None, None)

db_queue: "Queue" = Queue(maxsize=QUEUE_MAXSIZE)
_db_thread = None
_dropped = 0
_dropped_lock = threading.Lock()

logger = logging.getLogger("Database")


# ===================== SCHEMAT =====================
def _init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plc_status (
            plc_id TEXT PRIMARY KEY,
            status TEXT,
            timestamp DATETIME,
            image_path TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plc_id TEXT,
            ts DATETIME,
            pin TEXT,
            body_type TEXT,
            body_code TEXT,
            aux TEXT,
            result TEXT,
            image_path TEXT,
            duration_ms INTEGER
        )
    """)
    for sql in INDEXES:
        try:
            cur.execute(sql)
        except sqlite3.Error:
            logger.exception("Nie utworzono indeksu: %s", sql[:70])
    conn.commit()


def _open_connection() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=DB_TIMEOUT)
    try:
        _init_schema(conn)
    except sqlite3.Error:
        conn.close()
        raise
    return conn


# ===================== WORKER =====================
def _collect_batch():
    """Zwraca (paczka, czy_zatrzymac). Jeden commit na paczke."""
    try:
        item = db_queue.get(timeout=GET_TIMEOUT)
    except Empty:
        return [], False

    batch = []
    while True:
        if item[0] is None:
            return batch, True
        batch.append(item)
        if len(batch) >= BATCH_MAX:
            return batch, False
        try:
            item = db_queue.get_nowait()
        except Empty:
            return batch, False


def _safe_rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        logger.exception("DB ROLLBACK ERROR")


def _execute_batch(conn: sqlite3.Connection, batch: list) -> None:
    cur = conn.cursor()
    ok = 0
    for sql, params in batch:
        try:
            cur.execute(sql, params)
            ok += 1
        except sqlite3.Error:
            logger.exception("DB WRITE ERROR | SQL=%s", str(sql)[:60])
    if not ok:
        return
    try:
        conn.commit()
    except sqlite3.Error:
        logger.exception("DB COMMIT ERROR (rekordow w paczce: %d)", ok)
        _safe_rollback(conn)


def _db_worker() -> None:
    try:
        conn = _open_connection()
    except sqlite3.Error:
        logger.exception("DB INIT ERROR (%s)", DB_FILE)
        return

    logger.info("DB WORKER STARTED (%s, batch<=%d)", DB_FILE, BATCH_MAX)
    stopping = False
    try:
        while not stopping:
            batch, stopping = _collect_batch()
            if batch:
                _execute_batch(conn, batch)
    finally:
        conn.close()
        logger.info("DB WORKER STOPPED (odrzuconych zapisow: %d)", _dropped)


# ===================== STEROWANIE =====================
def start() -> None:
    global _db_thread
    if _db_thread and _db_thread.is_alive():
        return
    _db_thread = threading.Thread(target=_db_worker, daemon=True, name="db-worker")
    _db_thread.start()


def stop(timeout: float = 10.0) -> None:
    """Prosi watek DB o zakonczenie i CZEKA na dokonczenie zapisow."""
    if not (_db_thread and _db_thread.is_alive()):
        return
    try:
        db_queue.put(_STOP, timeout=2)
    except Full:
        logger.warning("Kolejka pelna - sygnal stop moze byc opozniony")
    _db_thread.join(timeout=timeout)
    if _db_thread.is_alive():
        logger.warning("Watek DB nie zakonczyl sie w %.0f s", timeout)


def queue_size() -> int:
    return db_queue.qsize()


def dropped_count() -> int:
    return _dropped


def _enqueue(sql: str, params: tuple) -> None:
    global _dropped
    try:
        db_queue.put_nowait((sql, params))
    except Full:
        with _dropped_lock:
            _dropped += 1
            dropped = _dropped
        if dropped % 100 == 1:
            logger.error("KOLEJKA DB PELNA (%d) - odrzucono zapis #%d", QUEUE_MAXSIZE, dropped)


# ===================== API ZAPISU =====================
def save_status(plc_id, status, image_path) -> None:
    _enqueue(
        """INSERT INTO plc_status (plc_id,status,timestamp,image_path)
           VALUES (?,?,?,?)
           ON CONFLICT(plc_id) DO UPDATE SET
           status=excluded.status,
           timestamp=excluded.timestamp,
           image_path=excluded.image_path""",
        (plc_id, status, datetime.now().isoformat(), image_path),
    )


def save_inspection(*row) -> None:
    if len(row) != 9:
        logger.error("save_inspection: oczekiwano 9 pol, otrzymano %d - pomijam", len(row))
        return
    _enqueue(
        """INSERT INTO inspections
           (plc_id, ts, pin, body_type, body_code, aux, result, image_path, duration_ms)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        row,
    )
