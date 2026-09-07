import os
import re
import time
import shutil
import logging
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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
TRANSCRIPTIONS_DIR = os .path.join(BASE_DIR, "transcriptions")

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
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "company"),
    "user": os.getenv("DB_USER", "company_user"),
    "password": os.getenv("DB_PASSWORD", "company_password"),
}


AUDIO_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma')

# Паттерны имен файлов АТС
# 1. Входящие: rg-5555-87014662990-20201113-150553-1605258353.53425.wav
PATTERN_INCOMING = re.compile(
    r"^(?:rg|in|incoming)-\d+-(\d+)-(\d{8})-(\d{6})-(\d+\.\d+)\.([a-z0-9]+)$",
    re.IGNORECASE
)

# 2. Исходящие: out-87479049469-1004-20201209-103647-1607488607.77991.wav
PATTERN_OUTGOING = re.compile(
    r"^(?:out|outgoing)-(\d+)-\d+-(\d{8})-(\d{6})-(\d+\.\d+)\.([a-z0-9]+)$",
    re.IGNORECASE
)

# 3. Общий паттерн звонков с датой и временем
PATTERN_GENERAL = re.compile(
    r"^(?:[a-z0-9_-]+-)?(\d{10,12})-.*?(\d{8})-(\d{6})-(\d+\.\d+)\.([a-z0-9]+)$",
    re.IGNORECASE
)


def parse_filename(filename, file_path=None):
    filename = os.path.basename(filename)
    extension = os.path.splitext(filename)[1].lstrip(".").lower()

    # 1. Проверяем паттерн входящего звонка (rg-...)
    m_in = PATTERN_INCOMING.match(filename)
    if m_in:
        phone, date_str, time_str, unique_id, _ = m_in.groups()
        call_date = datetime.strptime(date_str, "%Y%m%d").date()
        call_time = datetime.strptime(time_str, "%H%M%S").time()
        ts_dt = datetime.fromtimestamp(float(unique_id), tz=timezone.utc)
        return {
            "phone": phone,
            "call_date": call_date,
            "call_time": call_time,
            "unique_id": unique_id,
            "unix_timestamp": ts_dt,
            "extension": extension,
            "direction": "in"
        }

    # 2. Проверяем паттерн исходящего звонка (out-...)
    m_out = PATTERN_OUTGOING.match(filename)
    if m_out:
        phone, date_str, time_str, unique_id, _ = m_out.groups()
        call_date = datetime.strptime(date_str, "%Y%m%d").date()
        call_time = datetime.strptime(time_str, "%H%M%S").time()
        ts_dt = datetime.fromtimestamp(float(unique_id), tz=timezone.utc)
        return {
            "phone": phone,
            "call_date": call_date,
            "call_time": call_time,
            "unique_id": unique_id,
            "unix_timestamp": ts_dt,
            "extension": extension,
            "direction": "out"
        }

    # 3. Проверяем общий паттерн
    m_gen = PATTERN_GENERAL.match(filename)
    if m_gen:
        phone, date_str, time_str, unique_id, _ = m_gen.groups()
        call_date = datetime.strptime(date_str, "%Y%m%d").date()
        call_time = datetime.strptime(time_str, "%H%M%S").time()
        ts_dt = datetime.fromtimestamp(float(unique_id), tz=timezone.utc)
        return {
            "phone": phone,
            "call_date": call_date,
            "call_time": call_time,
            "unique_id": unique_id,
            "unix_timestamp": ts_dt,
            "extension": extension,
            "direction": "out" if filename.lower().startswith("out") else "in"
        }

    # 4. Универсальный fallback для любых аудиофайлов
    phone_match = re.search(r"\b(\d{10,12})\b", filename)
    phone = phone_match.group(1) if phone_match else "Клиент"

    date_match = re.search(r"\b(20\d{6})\b", filename)
    time_match = re.search(r"\b(\d{6})\b", filename)

    now = datetime.now()
    call_date = datetime.strptime(date_match.group(1), "%Y%m%d").date() if date_match else now.date()
    call_time = datetime.strptime(time_match.group(1), "%H%M%S").time() if time_match else now.time()

    uid_match = re.search(r"(\d{10}\.\d+)", filename)
    if uid_match:
        unique_id = uid_match.group(1)
        ts_dt = datetime.fromtimestamp(float(unique_id), tz=timezone.utc)
    else:
        mtime = os.path.getmtime(file_path) if file_path and os.path.exists(file_path) else now.timestamp()
        unique_id = f"{int(mtime)}.{abs(hash(filename)) % 100000}"
        ts_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)

    return {
        "phone": phone,
        "call_date": call_date,
        "call_time": call_time,
        "unique_id": unique_id,
        "unix_timestamp": ts_dt,
        "extension": extension,
        "direction": "out" if filename.lower().startswith("out") else "in"
    }


def save_to_database(data, filename, text):
    connection = psycopg2.connect(**DB_CONFIG)
    cursor = connection.cursor()

    # Автоматическое определение имени менеджера из текста
    manager_name = "Оператор"
    if text:
        m = re.search(r'[«"]?[Ээ]йкос[»"]?\s*[,–-]?\s*([А-ЯЁ][а-яё]{2,})', text)
        if m and m.group(1).lower() not in ["звоню", "слушаю", "добрый", "здравствуйте", "день", "компания"]:
            manager_name = m.group(1).capitalize()
        elif re.search(r'меня\s+([А-ЯЁ][а-яё]{2,})\s+зовут', text, re.I):
            manager_name = re.search(r'меня\s+([А-ЯЁ][а-яё]{2,})\s+зовут', text, re.I).group(1).capitalize()
        elif re.search(r'меня\s+зовут\s+([А-ЯЁ][а-яё]{2,})', text, re.I):
            manager_name = re.search(r'меня\s+зовут\s+([А-ЯЁ][а-яё]{2,})', text, re.I).group(1).capitalize()
        else:
            names = ["Жандос", "Руслан", "Азамат", "Данияр", "Айбек", "Ербол", "Максим", "Алексей"]
            for n in names:
                if re.search(rf'\b{n}\b', text[:300], re.I):
                    manager_name = n
                    break

    query = """
        INSERT INTO call_transcriptions (
            phone,
            call_date,
            call_time,
            unique_id,
            unix_timestamp,
            text,
            source_file,
            status,
            manager_name
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (unique_id)
        DO UPDATE SET 
            text = EXCLUDED.text,
            status = EXCLUDED.status,
            source_file = EXCLUDED.source_file,
            manager_name = EXCLUDED.manager_name
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
            manager_name,
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


def transcribe_with_gemini(file_path, output_txt_path=None):
    from dotenv import load_dotenv
    load_dotenv()
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Переменная GEMINI_API_KEY не найдена в файле .env")

    from google import genai
    client = genai.Client(api_key=api_key)

    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Файл не найден: {abs_path}")

    filename = os.path.basename(abs_path)
    logging.info(f"Загрузка аудиофайла {filename} на серверы Google Gemini...")
    audio_file = client.files.upload(file=abs_path)
    logging.info(f"Файл {filename} загружен в Gemini API. Имя объекта: {audio_file.name}")

    prompt = (
        "Сделай точную построчную транскрипцию этого телефонного разговора. "
        "Язык разговора может быть казахским, русским или смешанным (казахский + русский). "
        "Обязательно раздели диалог на реплики Менеджера и Клиента:\n"
        "**Менеджер:** <реплика>\n"
        "**Клиент:** <реплика>\n"
        "Пиши точно на том языке, на котором говорят участники (казахский/русский), не переводи и не добавляй вводных слов от себя, верни только диалог."
    )

    models_to_try = ["gemini-2.5-flash", "gemini-1.5-flash"]
    text = None

    for model_name in models_to_try:
        try:
            logging.info(f"Вызов Gemini API ({model_name}) для транскрибации...")
            response = client.models.generate_content(
                model=model_name,
                contents=[audio_file, prompt]
            )
            text = response.text.strip()
            if text:
                logging.info(f"Успешный ответ от {model_name}.")
                break
        except Exception as e:
            logging.warning(f"Ошибка при вызове {model_name}: {e}")

    # Удаляем временный файл на сервере Google
    try:
        client.files.delete(name=audio_file.name)
    except Exception:
        pass

    if not text:
        raise RuntimeError("Не удалось получить транскрипцию от Gemini API.")

    # Сохраняем в текстовый файл
    if output_txt_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_txt_path)), exist_ok=True)
        with open(output_txt_path, "w", encoding="utf-8") as f:
            f.write(text)

    return text


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

        # 3. Транскрибация звонка через Google Gemini 3.6 Flash
        logging.info(f"Запуск транскрибации через Gemini 3.6 Flash для {filename}...")
        text = transcribe_with_gemini(file_path, output_txt_path)

        logging.info(f"Транскрибация завершена. Длина текста: {len(text)} символов.")

        # 4. Записываем полученные метаданные и текст в БД PostgreSQL
        inserted_id = save_to_database(
            data,
            filename,
            text
        )

        # 6. Запускаем полный анализ Tone of Voice и сохраняем 5 уровней в БД
        if inserted_id:
            logging.info(f"Файл {filename} успешно обработан. Запись сохранена в PostgreSQL (ID: {inserted_id}).")
            try:
                from ton_voice_processor import process_all_tone_voice
                process_all_tone_voice(inserted_id, file_path, text)
            except Exception as e:
                logging.error(f"Ошибка при анализе Tone of Voice для {filename}: {e}", exc_info=True)
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

        # Проверяем расширение аудиофайла
        ext = os.path.splitext(event.src_path)[1].lower()
        if ext not in AUDIO_EXTENSIONS:
            return

        # Отправляем файл на обработку
        process_file(event.src_path)


# Точка входа в программу
def main():
    logging.info("Запуск COMPANY AI - CALL WATCHER (Входящие и Исходящие звонки)")
    
    # Создаем необходимые директории (входящие файлы и текстовые транскрипции)
    os.makedirs(INCOMING_DIR, exist_ok=True)
    os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)

    logging.info(f"Отслеживается папка: {INCOMING_DIR}")

    # Обработка уже существующих файлов в папке incoming при старте скрипта
    try:
        existing_files = os.listdir(INCOMING_DIR)
        files = [f for f in existing_files if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]
        if files:
            logging.info(f"Найдено существующих аудиофайлов при старте: {len(files)}")
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

    logging.info("Ожидание новых аудиофайлов (входящие rg-*, исходящие out-*, любые audio)...")

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