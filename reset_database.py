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

    # Таблицы для полной очистки каскадом (call_audit_logs не трогаем, чтобы не терять разметку РОПа)
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
            cur.execute(f"TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE;")
            logging.info(f"[+] Таблица {tbl} успешно очищена.")
        except Exception as e:
            logging.warning(f"[-] Не удалось очистить таблицу {tbl}: {e}")
            conn.rollback()
        else:
            conn.commit()

    cur.close()
    conn.close()

    # Очистка фронтенд JSON файлов
    base_dir = os.path.dirname(os.path.abspath(__file__))
    calls_json = os.path.join(base_dir, "frontend", "calls_data.json")
    audit_json = os.path.join(base_dir, "frontend", "audit_data.json")

    for f_path in [calls_json, audit_json]:
        try:
            with open(f_path, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
            logging.info(f"[+] Файл {f_path} очищен (установлен пустой список []).")
        except Exception as e:
            logging.warning(f"[-] Ошибка очистки файла {f_path}: {e}")

    logging.info("=== База данных и фронтенд успешно сброшены в исходное состояние (с нуля). ===")

if __name__ == "__main__":
    clear_all()
