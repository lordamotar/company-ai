import os
import re
import time
import shutil
import logging
import sys
from datetime import datetime, timezone

import psycopg2
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# Использовать C:\company-ai, если она существует, иначе локальную папку проекта
if os.path.exists(r"C:\Workspace\aikos.kz\company-ai\incoming"):
    BASE_DIR = r"C:\Workspace\aikos.kz\company-ai"
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INCOMING_DIR = os.path.join(BASE_DIR, "incoming")
PROCESSING_DIR = os.path.join(BASE_DIR, "processing")
PROCESSED_DIR = os.path.join(BASE_DIR, "processed")
ERROR_DIR = os.path.join(BASE_DIR, "error")
TRANSCRIPTIONS_DIR = os.path.join(BASE_DIR, "transcriptions")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "worker.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5433,
    "database": "company",
    "user": "company_user",
    "password": "company_password",
}


FILENAME_PATTERN = re.compile(
    r"^rg-\d+-(\d+)-(\d{8})-(\d{6})-(\d+\.\d+)\.(mp3|wav)$",
    re.IGNORECASE
)


def parse_filename(filename):
    match = FILENAME_PATTERN.match(filename)

    if not match:
        raise ValueError(
            f"Не удалось разобрать имя файла: {filename}"
        )

    phone = match.group(1)
    date_str = match.group(2)
    time_str = match.group(3)
    unique_id = match.group(4)
    extension = match.group(5)

    call_date = datetime.strptime(
        date_str, "%Y%m%d"
    ).date()

    call_time = datetime.strptime(
        time_str, "%H%M%S"
    ).time()

    unix_timestamp = float(unique_id)

    timestamp_dt = datetime.fromtimestamp(
        unix_timestamp,
        tz=timezone.utc
    )

    return {
        "phone": phone,
        "call_date": call_date,
        "call_time": call_time,
        "unique_id": unique_id,
        "unix_timestamp": timestamp_dt,
        "extension": extension,
    }


def save_to_database(data, filename, text):
    connection = psycopg2.connect(**DB_CONFIG)

    cursor = connection.cursor()

    query = """
        INSERT INTO call_transcriptions (
            phone,
            call_date,
            call_time,
            unique_id,
            unix_timestamp,
            text,
            source_file,
            status
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (unique_id)
        DO UPDATE SET 
            text = EXCLUDED.text,
            status = EXCLUDED.status,
            source_file = EXCLUDED.source_file
        RETURNING id;
    """

    cursor.execute(
        query,
        (
            data["phone"],
            data["call_date"],
            data["call_time"],
            data["unique_id"],
            data["unix_timestamp"],
            text,
            filename,
            "completed",
        )
    )

    result = cursor.fetchone()
    inserted_id = result[0] if result else None

    connection.commit()

    cursor.close()
    connection.close()
    return inserted_id


# Функция проверки, обрабатывался ли файл ранее по его уникальному ID
def is_already_processed(unique_id):
    connection = psycopg2.connect(**DB_CONFIG)
    cursor = connection.cursor()
    query = "SELECT text FROM call_transcriptions WHERE unique_id = %s;"
    cursor.execute(query, (unique_id,))
    result = cursor.fetchone()
    cursor.close()
    connection.close()
    
    if result is None:
        return False
        
    db_text = result[0]
    # Если текста нет или он является старой тестовой заглушкой,
    # разрешаем обработать файл заново для записи реального текста
    if not db_text or db_text == "ТЕСТОВАЯ ТРАНСКРИБАЦИЯ":
        return False
        
    return True


# Основная функция обработки медиафайла (mp3 или wav)
def process_file(file_path):
    filename = os.path.basename(file_path)
    logging.info(f"Начало обработки файла: {filename}")

    try:
        # 1. Извлекаем метаданные из имени файла (телефон, дата, время, ID)
        data = parse_filename(filename)
        
        # 2. Проверяем в БД, обрабатывали ли мы этот файл ранее
        if is_already_processed(data["unique_id"]):
            logging.info(f"Файл {filename} уже был успешно обработан ранее. Пропускаем.")
            return

        logging.info(f"Данные файла {filename}: Телефон={data['phone']}, Дата={data['call_date']}, Время={data['call_time']}, Unique ID={data['unique_id']}")

        # Путь для текстового результата транскрипции (в папку transcriptions)
        output_txt_name = os.path.splitext(filename)[0] + ".txt"
        output_txt_path = os.path.join(TRANSCRIPTIONS_DIR, output_txt_name)

        # 3. Вызываем локальный транскрибатор быстрее-whisper через корневое окружение .venv
        transcriber_dir = os.path.join(BASE_DIR, "transcriber")
        import subprocess

        # Путь к интерпретатору python в корневом .venv проекте
        python_exe = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
        cmd = [
            python_exe,
            "local_transcribe.py",
            file_path,
            output_txt_path
        ]

        logging.info(f"Запуск транскрибации для {filename}: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            cwd=transcriber_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )

        # Если скрипт транскрибации завершился с ошибкой, бросаем исключение
        if result.returncode != 0:
            raise RuntimeError(f"Ошибка транскрибатора (код {result.returncode}): {result.stderr or result.stdout}")

        # 4. Считываем распознанный текст из созданного файла
        if not os.path.exists(output_txt_path):
            raise FileNotFoundError(f"Файл транскрипции не был создан: {output_txt_path}")

        with open(output_txt_path, "r", encoding="utf-8") as f:
            text = f.read().strip()

        logging.info(f"Транскрибация завершена. Длина текста: {len(text)} символов.")

        # 5. Записываем полученные метаданные и текст в БД PostgreSQL
        inserted_id = save_to_database(
            data,
            filename,
            text
        )

        # Проверяем статус вставки в БД
        if inserted_id:
            logging.info(f"Файл {filename} успешно обработан. Запись сохранена в PostgreSQL (ID: {inserted_id}).")
        else:
            logging.info(f"Файл {filename} обработан. Запись с таким Unique ID ({data['unique_id']}) уже существовала в базе данных (пропущено).")

    except Exception as error:
        # В случае ошибки логируем ее
        logging.error(f"Ошибка при обработке файла {filename}: {error}", exc_info=True)


# Обработчик событий файловой системы watchdog
class MP3Handler(FileSystemEventHandler):

    def on_created(self, event):
        # Реагируем только на файлы, игнорируем папки
        if event.is_directory:
            return

        # Проверяем расширение файла (.mp3 или .wav)
        ext = event.src_path.lower()
        if not (ext.endswith(".mp3") or ext.endswith(".wav")):
            return

        # Отправляем файл на обработку
        process_file(event.src_path)


# Точка входа в программу
def main():
    logging.info("Запуск COMPANY AI - CALL WATCHER")
    
    # Создаем необходимые директории (входящие файлы и текстовые транскрипции)
    os.makedirs(INCOMING_DIR, exist_ok=True)
    os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)

    logging.info(f"Отслеживается папка: {INCOMING_DIR}")

    # Обработка уже существующих файлов в папке incoming при старте скрипта
    try:
        existing_files = os.listdir(INCOMING_DIR)
        files = [f for f in existing_files if f.lower().endswith('.mp3') or f.lower().endswith('.wav')]
        if files:
            logging.info(f"Найдено существующих файлов при старте: {len(files)}")
            for file_name in files:
                file_path = os.path.join(INCOMING_DIR, file_name)
                process_file(file_path)
    except Exception as e:
        logging.error(f"Ошибка при сканировании папки входящих файлов при старте: {e}")

    # Инициализируем watchdog для отслеживания событий создания файлов в реальном времени
    event_handler = MP3Handler()
    observer = Observer()
    observer.schedule(
        event_handler,
        INCOMING_DIR,
        recursive=False
    )
    observer.start()

    logging.info("Ожидание новых MP3/WAV файлов...")

    try:
        # Бесконечный цикл работы службы
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        # Безопасное завершение при прерывании
        logging.info("Остановка службы...")
        observer.stop()

    observer.join()
    logging.info("Служба остановлена.")


if __name__ == "__main__":
    main()