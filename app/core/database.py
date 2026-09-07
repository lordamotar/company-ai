import psycopg2
from contextlib import contextmanager
from app.core.config import settings

@contextmanager
def get_db_connection():
    """Контекстный менеджер для безопасного открытия и закрытия соединений с PostgreSQL."""
    conn = psycopg2.connect(**settings.DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()
