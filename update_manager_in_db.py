import psycopg2
import sys

# Настройка кодировки вывода
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5433,
    "database": "company",
    "user": "company_user",
    "password": "company_password",
}

def update_managers():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # 1. Добавляем колонку manager_name в call_transcriptions
    cur.execute("ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS manager_name VARCHAR(64);")
    
    # 2. Добавляем колонку manager_name в customer_sentiment
    cur.execute("ALTER TABLE customer_sentiment ADD COLUMN IF NOT EXISTS manager_name VARCHAR(64);")

    # 3. Обновляем звонок номер 2 на Жандос
    cur.execute("UPDATE call_transcriptions SET manager_name = %s WHERE id = %s;", ('Жандос', 2))
    cur.execute("UPDATE customer_sentiment SET manager_name = %s WHERE call_id = %s;", ('Жандос', 2))

    # 4. Обновляем звонок номер 1 на Руслан
    cur.execute("UPDATE call_transcriptions SET manager_name = %s WHERE id = %s;", ('Руслан', 1))
    cur.execute("UPDATE customer_sentiment SET manager_name = %s WHERE call_id = %s;", ('Руслан', 1))

    conn.commit()

    cur.execute("SELECT id, phone, manager_name FROM call_transcriptions ORDER BY id;")
    rows = cur.fetchall()
    print("Успешно обновлены данные в PostgreSQL:")
    for r in rows:
        print(f"  ID={r[0]} | Телефон={r[1]} | Менеджер={r[2]}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    update_managers()
