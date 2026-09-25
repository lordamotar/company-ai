"""
Скрипт восстановления ручных правок РОПа из журнала аудита (call_audit_logs)
Восстанавливает имена менеджеров и города, которые могли быть затерты автоматическим воркером.
"""
import os
import sys
import logging
import psycopg2
from export_db_to_json import DB_CONFIG, export_calls_to_json, export_audit_logs_to_json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

def restore_rop_edits():
    logging.info("=== Запуск восстановления правок РОПа из call_audit_logs ===")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        # Убеждаемся в наличии колонки unique_id
        cur.execute("ALTER TABLE call_audit_logs ADD COLUMN IF NOT EXISTS unique_id VARCHAR(64);")

        # 1. Ищем последние ручные правки менеджеров (приоритет по unique_id, затем call_id)
        cur.execute("""
            SELECT DISTINCT ON (COALESCE(unique_id, call_id::text)) 
                unique_id, call_id, new_value, changed_by, changed_at
            FROM call_audit_logs
            WHERE field_name = 'manager_name'
              AND new_value IS NOT NULL 
              AND new_value != '' 
              AND new_value != '—'
              AND new_value != 'Оператор'
            ORDER BY COALESCE(unique_id, call_id::text), id DESC;
        """)
        mgr_rows = cur.fetchall()
        logging.info(f"Найдено {len(mgr_rows)} звонков с ручными правками менеджеров в журнале аудита.")

        restored_mgr_count = 0
        for uid, call_id, rop_manager, changed_by, changed_at in mgr_rows:
            if uid:
                cur.execute("SELECT id, manager_name FROM call_transcriptions WHERE unique_id = %s;", (uid,))
            else:
                cur.execute("SELECT id, manager_name FROM call_transcriptions WHERE id = %s;", (call_id,))
            current_row = cur.fetchone()
            if not current_row:
                continue

            target_id, current_mgr = current_row[0], current_row[1]

            if current_mgr != rop_manager:
                cur.execute("""
                    UPDATE call_transcriptions 
                    SET manager_name = %s 
                    WHERE id = %s;
                """, (rop_manager, target_id))
                restored_mgr_count += 1
                logging.info(f"  [+] Звонок #{target_id} (uid={uid or call_id}): менеджер восстановлен '{current_mgr}' -> '{rop_manager}' (автор правки: {changed_by})")

        # 2. Ищем последние ручные правки городов
        cur.execute("""
            SELECT DISTINCT ON (COALESCE(unique_id, call_id::text)) 
                unique_id, call_id, new_value, changed_by, changed_at
            FROM call_audit_logs
            WHERE field_name = 'city_name'
              AND new_value IS NOT NULL 
              AND new_value != '' 
              AND new_value != '—'
            ORDER BY COALESCE(unique_id, call_id::text), id DESC;
        """)
        city_rows = cur.fetchall()
        logging.info(f"Найдено {len(city_rows)} звонков с ручными правками городов в журнале аудита.")

        restored_city_count = 0
        for uid, call_id, rop_city, changed_by, changed_at in city_rows:
            if uid:
                cur.execute("SELECT id, city_name FROM call_transcriptions WHERE unique_id = %s;", (uid,))
            else:
                cur.execute("SELECT id, city_name FROM call_transcriptions WHERE id = %s;", (call_id,))
            current_row = cur.fetchone()
            if not current_row:
                continue

            target_id, current_city = current_row[0], current_row[1]

            if current_city != rop_city:
                cur.execute("""
                    UPDATE call_transcriptions 
                    SET city_name = %s 
                    WHERE id = %s;
                """, (rop_city, target_id))
                restored_city_count += 1
                logging.info(f"  [+] Звонок #{target_id} (uid={uid or call_id}): город восстановлен '{current_city}' -> '{rop_city}' (автор правки: {changed_by})")

        conn.commit()
        logging.info(f"=== Успешно восстановлено: менеджеров = {restored_mgr_count}, городов = {restored_city_count} ===")

        # 3. Синхронизируем JSON для фронтенда
        logging.info("Синхронизация JSON-файлов для фронтенда...")
        export_calls_to_json()
        export_audit_logs_to_json()
        logging.info("Фронтенд-данные успешно обновлены.")

    except Exception as e:
        conn.rollback()
        logging.error(f"[-] Ошибка при восстановлении правок: {e}", exc_info=True)
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    restore_rop_edits()
