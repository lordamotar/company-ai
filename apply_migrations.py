import psycopg2
import sys

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5433,
    "database": "company",
    "user": "company_user",
    "password": "company_password",
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
    apply_migration()
