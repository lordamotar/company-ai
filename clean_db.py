import psycopg2
from export_db_to_json import DB_CONFIG, export_calls_to_json

def clean_database():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        print("Очистка всех таблиц звонков и Tone of Voice...")
        cursor.execute("""
            TRUNCATE TABLE 
                call_transcriptions, 
                call_audio_features, 
                call_audio_emotions, 
                call_audio_segments, 
                call_text_sentiment_lines, 
                customer_sentiment 
            RESTART IDENTITY CASCADE;
        """)
        conn.commit()
        cursor.close()
        conn.close()

        print("[+] Все таблицы в PostgreSQL успешно очищены, счетчики ID сброшены на 1.")

        # Обновляем calls_data.json
        export_calls_to_json()
        print("[+] Файл html/calls_data.json очищен.")
    except Exception as e:
        print(f"[!] Ошибка при очистке БД: {e}")

if __name__ == "__main__":
    clean_database()
