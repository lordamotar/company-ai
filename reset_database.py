import os
import json
import psycopg2
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "company"),
    "user": os.getenv("DB_USER", "company_user"),
    "password": os.getenv("DB_PASSWORD", "company_password"),
}

def clear_all():
    logging.info("Подключение к базе данных PostgreSQL...")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Убеждаемся, что в call_audit_logs есть unique_id и нет каскадного FK на call_transcriptions
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS call_audit_logs (
                id SERIAL PRIMARY KEY,
                call_id INT,
                unique_id VARCHAR(64),
                field_name VARCHAR(64),
                old_value TEXT,
                new_value TEXT,
                changed_by VARCHAR(64),
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                comment TEXT
            );
            ALTER TABLE call_audit_logs ADD COLUMN IF NOT EXISTS unique_id VARCHAR(64);
            DO $$
            DECLARE r RECORD;
            BEGIN
                FOR r IN (
                    SELECT constraint_name 
                    FROM information_schema.table_constraints 
                    WHERE table_name = 'call_audit_logs' AND constraint_type = 'FOREIGN KEY'
                ) LOOP
                    EXECUTE 'ALTER TABLE call_audit_logs DROP CONSTRAINT IF EXISTS ' || quote_ident(r.constraint_name);
                END LOOP;
            END $$;
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.warning(f"[-] Проверка структуры call_audit_logs: {e}")

    # Таблицы для полной очистки каскадом БЕЗ RESTART IDENTITY (call_audit_logs не трогаем, чтобы не терять разметку РОПа)
    tables = [
        "call_text_sentiment_lines",
        "call_audio_segments",
        "call_audio_emotions",
        "call_audio_features",
        "customer_sentiment",
        "call_transcriptions"
    ]

    for tbl in tables:
        try:
            # Очищаем без RESTART IDENTITY, чтобы call_id не сбивали нумерацию и не привязывались к чужим записям
            cur.execute(f"TRUNCATE TABLE {tbl} CASCADE;")
            logging.info(f"[+] Таблица {tbl} успешно очищена.")
        except Exception as e:
            logging.warning(f"[-] Не удалось очистить таблицу {tbl}: {e}")
            conn.rollback()
        else:
            conn.commit()

    cur.close()
    conn.close()

    # Очистка фронтенд JSON файла звонков (audit_data.json сохраняем)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    calls_json = os.path.join(base_dir, "frontend", "calls_data.json")

    try:
        with open(calls_json, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        logging.info(f"[+] Файл {calls_json} очищен (установлен пустой список []).")
    except Exception as e:
        logging.warning(f"[-] Ошибка очистки файла {calls_json}: {e}")

    try:
        from export_db_to_json import export_audit_logs_to_json
        export_audit_logs_to_json(write_to_file=True)
        logging.info("[+] Журнал аудита сохранен и синхронизирован с frontend/audit_data.json.")
    except Exception as e:
        logging.warning(f"[-] Ошибка обновления audit_data.json: {e}")

    logging.info("=== База данных и фронтенд успешно сброшены в исходное состояние (с нуля). Журнал аудита РОПа сохранен. ===")

if __name__ == "__main__":
    clear_all()
