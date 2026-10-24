import os
import sys
import psycopg2

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "company"),
    "user": os.getenv("DB_USER", "company_user"),
    "password": os.getenv("DB_PASSWORD", "company_password"),
}

def apply_migration(sql_file="init_tone_tables.sql"):
    print(f"Применение миграции из файла {sql_file}...")
    try:
        with open(sql_file, "r", encoding="utf-8") as f:
            sql_content = f.read()

        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(sql_content)
        conn.commit()
        
        # Получаем список всех таблиц в базе
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            ORDER BY table_name;
        """)
        tables = cursor.fetchall()
        
        print("\n[+] Миграция успешно применена!")
        print("Текущие таблицы в базе данных:")
        for t in tables:
            print(f" - {t[0]}")

        cursor.close()
        conn.close()
    except Exception as e:
        print(f"\n[!] Ошибка при выполнении миграции: {e}")
        print("Убедитесь, что Docker-контейнер PostgreSQL запущен командой: docker compose up -d")

if __name__ == "__main__":
    # По умолчанию — основная миграция; дополнительная: python apply_migrations.py migrations/<файл>.sql
    apply_migration(sys.argv[1] if len(sys.argv) > 1 else "init_tone_tables.sql")
