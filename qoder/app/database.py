import logging
from contextlib import contextmanager
from typing import Generator

import pymysql
from pymysql.cursors import DictCursor

from app.config import settings

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """数据库不可用或连接失败。"""


def _connect() -> pymysql.Connection:
    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=5,
        read_timeout=30,
    )


@contextmanager
def get_connection() -> Generator[pymysql.Connection, None, None]:
    conn = None
    try:
        conn = _connect()
        yield conn
    except pymysql.Error as exc:
        logger.error("数据库连接错误: %s", exc)
        raise DatabaseError("数据库不可用") from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@contextmanager
def get_cursor(commit: bool = False) -> Generator[pymysql.cursors.DictCursor, None, None]:
    """获取游标。commit=True 时正常退出自动 commit，异常时 rollback。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            yield cursor
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


def check_health() -> dict:
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            row = cur.fetchone()
            return {"status": "ok", "database": settings.db_name, "check": row.get("ok")}
    except DatabaseError as exc:
        return {"status": "error", "database": settings.db_name, "error": "数据库不可用"}
