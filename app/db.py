"""Подключение к базе данных.

По умолчанию — SQLite-файл: на хостинге Amvera в постоянном томе /data,
локально — в папке data/ рядом с кодом. DATABASE_URL переопределяет адрес базы.
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def _default_url() -> str:
    if os.environ.get("DATA_DIR"):
        data_dir = Path(os.environ["DATA_DIR"])
    elif os.environ.get("AMVERA") or (os.path.isdir("/data") and os.access("/data", os.W_OK)):
        data_dir = Path("/data")  # постоянный том хостинга: база переживает перезапуск
    else:
        data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data_dir / 'dekanat.db'}"


DATABASE_URL = os.environ.get("DATABASE_URL") or _default_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    pool_pre_ping=True,
)

if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
