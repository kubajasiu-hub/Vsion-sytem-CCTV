"""
cleanup.py
==========
Retencja danych: stare obrazy z dysku + stare rekordy z bazy.
Sciezki z config.py (VISION_DB_FILE / VISION_STATIC_DIR) - te same, ktorych uzywa backend.

Uruchomienie reczne:  python cleanup.py --dry-run | --now
"""
import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from config import DB_FILE, IMAGES_ROOT

# --- Retencja (dni) ---
RETENTION_DAYS = 7         # rekordy i zdjecia OK
RETENTION_DAYS_NOK = 30    # rekordy i zdjecia NOK
RETENTION_DAYS_RAW = 7     # pliki *_RAW.jpg

# --- Harmonogram ---
CLEANUP_HOUR = 9
CLEANUP_MINUTE = 15

# --- VACUUM ---
VACUUM_MIN_DELETED_ROWS = 5000
VACUUM_WEEKDAY = 6         # niedziela

DB_TIMEOUT = 30

# archive.py: res = result[:2] -> "NOK" w nazwie pliku jako "_NO"
NOK_SUFFIXES = ("_NO.jpg", "_NOK.jpg")

logger = logging.getLogger("Cleanup")


# ===================== POMOCNICZE =====================
def _day_cutoff(days: int) -> datetime:
    """Granica retencji zaokraglona do poczatku dnia (foldery obrazow sa dzienne)."""
    return (datetime.now() - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _human(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} PB"


def _dir_size(path: Path) -> int:
    total = 0
    for file in path.rglob("*"):
        try:
            if file.is_file():
                total += file.stat().st_size
        except OSError:
            continue
    return total


def _iter_day_folders(older_than: datetime):
    """(data, sciezka) folderow dnia YYYY-MM-DD starszych niz granica."""
    if not IMAGES_ROOT.is_dir():
        logger.warning("IMAGES_ROOT nie istnieje: %s", IMAGES_ROOT)
        return
    for entry in sorted(IMAGES_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        try:
            folder_date = datetime.strptime(entry.name, "%Y-%m-%d")
        except ValueError:
            continue
        if folder_date < older_than:
            yield folder_date, entry


def _remove_file(path: Path) -> int:
    """Usuwa plik, zwraca liczbe zwolnionych bajtow (0 przy bledzie)."""
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except OSError:
        logger.warning("Nie usunieto %s", path, exc_info=True)
        return 0


def _remove_empty_subdirs(day_path: Path) -> None:
    for sub in day_path.iterdir():
        if sub.is_dir() and not any(sub.iterdir()):
            try:
                sub.rmdir()
            except OSError:
                logger.warning("Nie usunieto pustego katalogu %s", sub, exc_info=True)


# ===================== OBRAZY =====================
def clean_old_raw_files() -> int:
    """Kasuje *_RAW.jpg starsze niz RETENTION_DAYS_RAW (zdjecia wynikowe zostaja)."""
    if RETENTION_DAYS_RAW >= RETENTION_DAYS:
        return 0          # RAW i tak zniknie przy przerzedzaniu folderow
    removed = 0
    for _date, day_path in _iter_day_folders(_day_cutoff(RETENTION_DAYS_RAW)):
        for raw in day_path.rglob("*_RAW.jpg"):
            if _remove_file(raw):
                removed += 1
    if removed:
        logger.info("Usunieto plikow RAW: %d", removed)
    return removed


def _thin_day_folder(day_path: Path) -> tuple:
    """Zostawia w folderze dnia tylko zdjecia NOK. Zwraca (usuniete_foldery, bajty)."""
    freed = 0
    removed = 0
    for file in day_path.rglob("*"):
        if file.is_file() and not file.name.endswith(NOK_SUFFIXES):
            size = _remove_file(file)
            freed += size
            removed += 1 if size else 0
    _remove_empty_subdirs(day_path)
    if removed:
        logger.info("Przerzedzono %s: %d plikow (%s), zachowano NOK", day_path, removed, _human(freed))

    if not any(day_path.iterdir()):
        day_path.rmdir()
        logger.info("Usunieto pusty folder: %s", day_path)
        return 1, freed
    return 0, freed


def _remove_day_folder(day_path: Path) -> tuple:
    size = _dir_size(day_path)
    shutil.rmtree(day_path)
    logger.info("Usunieto folder (>%dd): %s (%s)", RETENTION_DAYS_NOK, day_path, _human(size))
    return 1, size


def clean_old_image_folders() -> tuple:
    """
    Dwustopniowa retencja:
      1) starsze niz RETENTION_DAYS_NOK -> caly folder dnia
      2) starsze niz RETENTION_DAYS     -> tylko zdjecia NOK zostaja
    Zwraca (usuniete_foldery, odzyskane_bajty).
    """
    cutoff_nok = _day_cutoff(RETENTION_DAYS_NOK)
    removed_dirs = 0
    freed = 0
    for folder_date, day_path in _iter_day_folders(_day_cutoff(RETENTION_DAYS)):
        action = _remove_day_folder if folder_date < cutoff_nok else _thin_day_folder
        try:
            dirs, size = action(day_path)
        except OSError:
            logger.exception("Blad retencji folderu %s", day_path)
            continue
        removed_dirs += dirs
        freed += size
    return removed_dirs, freed


# ===================== BAZA =====================
def _delete_old_records(cur: sqlite3.Cursor) -> tuple:
    cutoff_ok = _day_cutoff(RETENTION_DAYS).isoformat()
    cutoff_nok = _day_cutoff(RETENTION_DAYS_NOK).isoformat()

    cur.execute(
        "DELETE FROM inspections WHERE ts < ? AND (result IS NULL OR result NOT LIKE '%NOK%')",
        (cutoff_ok,),
    )
    deleted_ok = max(0, cur.rowcount or 0)

    cur.execute(
        "DELETE FROM inspections WHERE ts < ? AND result LIKE '%NOK%'",
        (cutoff_nok,),
    )
    deleted_nok = max(0, cur.rowcount or 0)

    # osierocone wskazniki obrazow (stanowisko nie pracowalo > RETENTION_DAYS)
    cur.execute(
        "UPDATE plc_status SET image_path = NULL WHERE timestamp < ? AND image_path IS NOT NULL",
        (cutoff_ok,),
    )
    orphans = max(0, cur.rowcount or 0)
    return deleted_ok, deleted_nok, orphans


def clean_old_database() -> int:
    """Usuwa stare rekordy (osobna retencja OK / NOK). Zwraca liczbe usunietych wierszy."""
    conn = None
    try:
        conn = sqlite3.connect(str(DB_FILE), timeout=DB_TIMEOUT)
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.cursor()
        deleted_ok, deleted_nok, orphans = _delete_old_records(cur)
        conn.commit()
        logger.info(
            "DB: usunieto %d rekordow (OK/inne: %d, NOK: %d), wyzerowano image_path: %d",
            deleted_ok + deleted_nok, deleted_ok, deleted_nok, orphans,
        )
        try:
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            logger.warning("wal_checkpoint pominiety", exc_info=True)
        return deleted_ok + deleted_nok
    except sqlite3.Error:
        logger.exception("Blad czyszczenia bazy")
        return 0
    finally:
        if conn is not None:
            conn.close()


def maybe_vacuum(deleted_rows: int) -> bool:
    """VACUUM tylko w niedziele i po duzym czyszczeniu (wymaga wylacznosci na bazie)."""
    if deleted_rows < VACUUM_MIN_DELETED_ROWS or datetime.now().weekday() != VACUUM_WEEKDAY:
        return False
    conn = None
    try:
        size_before = DB_FILE.stat().st_size if DB_FILE.exists() else 0
        conn = sqlite3.connect(str(DB_FILE), timeout=DB_TIMEOUT)
        conn.isolation_level = None
        conn.execute("VACUUM")
        size_after = DB_FILE.stat().st_size if DB_FILE.exists() else 0
        logger.info(
            "VACUUM: %s -> %s (odzyskano %s)",
            _human(size_before), _human(size_after), _human(max(0, size_before - size_after)),
        )
        return True
    except sqlite3.Error:
        logger.exception("VACUUM nieudany (baza zajeta?)")
        return False
    finally:
        if conn is not None:
            conn.close()


# ===================== ZADANIE =====================
def run_cleanup() -> None:
    started = time.time()
    logger.info(
        "=== START cleanup (retencja: %dd | NOK %dd | RAW %dd) ===",
        RETENTION_DAYS, RETENTION_DAYS_NOK, RETENTION_DAYS_RAW,
    )
    raw_removed = clean_old_raw_files()
    dirs_removed, freed = clean_old_image_folders()
    deleted_rows = clean_old_database()
    vacuumed = maybe_vacuum(deleted_rows)
    logger.info(
        "=== KONIEC cleanup w %.1fs | foldery: %d (%s) | RAW: %d | rekordy: %d | VACUUM: %s ===",
        time.time() - started, dirs_removed, _human(freed), raw_removed, deleted_rows,
        "tak" if vacuumed else "nie",
    )


# ===================== SCHEDULER =====================
def _seconds_until_next_run() -> float:
    now = datetime.now()
    nxt = now.replace(hour=CLEANUP_HOUR, minute=CLEANUP_MINUTE, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def _scheduler() -> None:
    logger.info("Cleanup scheduler: nastepne uruchomienie za %.0f min", _seconds_until_next_run() / 60)
    while True:
        try:
            time.sleep(_seconds_until_next_run())
            run_cleanup()
            time.sleep(1)
        except Exception:  # noqa: BLE001 - watek nie moze umrzec
            logger.exception("Blad w schedulerze cleanup")
            time.sleep(60)


_cleanup_thread = None


def start_cleanup_thread() -> None:
    global _cleanup_thread
    if _cleanup_thread and _cleanup_thread.is_alive():
        return
    _cleanup_thread = threading.Thread(target=_scheduler, daemon=True, name="cleanup")
    _cleanup_thread.start()
    logger.info("Cleanup thread started (codziennie %02d:%02d)", CLEANUP_HOUR, CLEANUP_MINUTE)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    parser = argparse.ArgumentParser(description="Retencja danych inspekcji")
    parser.add_argument("--now", action="store_true", help="uruchom czyszczenie natychmiast")
    parser.add_argument("--dry-run", action="store_true", help="pokaz konfiguracje i granice retencji")
    args = parser.parse_args()

    print(f"DB_FILE     = {DB_FILE}")
    print(f"IMAGES_ROOT = {IMAGES_ROOT}")
    print(f"Granica RAW         : {_day_cutoff(RETENTION_DAYS_RAW)}")
    print(f"Granica standardowa : {_day_cutoff(RETENTION_DAYS)}")
    print(f"Granica NOK         : {_day_cutoff(RETENTION_DAYS_NOK)}")

    if args.now:
        run_cleanup()
    elif not args.dry_run:
        start_cleanup_thread()
        print("Scheduler uruchomiony. Ctrl+C aby zakonczyc.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("Zakonczono.")
