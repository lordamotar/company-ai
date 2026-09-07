import time
import logging
from export_db_to_json import export_calls_to_json, export_audit_logs_to_json

logger = logging.getLogger(__name__)

_LAST_EXPORT_TIME = 0.0

def sync_data_if_needed(force: bool = False, min_interval: float = 2.0):
    """
    Экспортирует актуальные данные из PostgreSQL в calls_data.json и audit_data.json.
    Использует дебаунсинг для предотвращения избыточной нагрузки на БД при частых запросах.
    """
    global _LAST_EXPORT_TIME
    now = time.time()
    if force or (now - _LAST_EXPORT_TIME > min_interval):
        try:
            _LAST_EXPORT_TIME = now
            export_calls_to_json()
            export_audit_logs_to_json()
        except Exception as e:
            logger.error(f"[!] Ошибка экспорта из БД: {e}")
