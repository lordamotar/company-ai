import os
import re
import time
import shutil
import logging
import sys
import json
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import psycopg2
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


BASE_DIR = os.getenv("BASE_DIR", os.path.dirname(os.path.abspath(__file__)))

INCOMING_DIR = os.path.join(BASE_DIR, "incoming")
PROCESSING_DIR = os.path.join(BASE_DIR, "processing")
PROCESSED_DIR = os.path.join(BASE_DIR, "processed")
ERROR_DIR = os.path.join(BASE_DIR, "error")
TRANSCRIPTIONS_DIR = os.path.join(BASE_DIR, "transcriptions")

# Блокировка от параллельной обработки одного и того же файла разными потоками
_processing_lock = threading.Lock()
_currently_processing_files = set()

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

# Параметры надежности и повторов
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
FILE_READY_TIMEOUT = int(os.getenv("FILE_READY_TIMEOUT", "30"))

# Список поддерживаемых моделей Google Gemini по приоритету (версии 2.5+)
DEFAULT_GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-flash-latest"
]

def get_configured_models():
    """Возвращает список моделей Gemini из окружения или дефолтный список."""
    env_val = os.getenv("GEMINI_MODELS", "")
    if env_val:
        models = [m.strip() for m in env_val.split(",") if m.strip()]
        if models:
            return models
    return DEFAULT_GEMINI_MODELS

# Справочник внутренних номеров АТС -> Имя сотрудника
DEFAULT_OPERATOR_EXTENSIONS = {
    "1001": "Жандос",
    "1002": "Руслан",
    "1003": "Азамат",
    "1004": "Данияр",
    "1005": "Айбек",
    "1006": "Ербол",
    "1007": "Максим",
    "1008": "Алексей"
}

def get_operator_by_extension(ext):
    """Ищет имя менеджера по внутреннему номеру оператора."""
    if not ext:
        return None
    ext_str = str(ext).strip()
    return DEFAULT_OPERATOR_EXTENSIONS.get(ext_str)

AUDIO_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma')

# Паттерны имен файлов АТС
# 1. Входящие: rg-5555-87014662990-20201113-150553-1605258353.53425.wav
PATTERN_INCOMING = re.compile(
    r"^(?:rg|in|incoming)-(\d+)-(\d+)-(\d{8})-(\d{6})-(\d+\.\d+)\.([a-z0-9]+)$",
    re.IGNORECASE
)

# 2. Исходящие: out-87479049469-1004-20201209-103647-1607488607.77991.wav
PATTERN_OUTGOING = re.compile(
    r"^(?:out|outgoing)-(\d+)-(\d+)-(\d{8})-(\d{6})-(\d+\.\d+)\.([a-z0-9]+)$",
    re.IGNORECASE
)

# 3. Внешние: external-1001-87051234567-20201113-150553-1605258353.53425.wav
PATTERN_EXTERNAL = re.compile(
    r"^(?:external)-(\d+)-(\d+)-(\d{8})-(\d{6})-(\d+\.\d+)\.([a-z0-9]+)$",
    re.IGNORECASE
)

# 4. Общий паттерн звонков с датой и временем
PATTERN_GENERAL = re.compile(
    r"^(?:[a-z0-9_-]+-)?(\d{10,12})-.*?(\d{8})-(\d{6})-(\d+\.\d+)\.([a-z0-9]+)$",
    re.IGNORECASE
)


def wait_for_file_ready(file_path, timeout=FILE_READY_TIMEOUT, check_interval=1.0):
    """
    Проверяет, что файл полностью записан на диск и не заблокирован внешним процессом.
    Сравнивает размер с интервалом, ожидая стабилизации.
    """
    if not os.path.exists(file_path):
        return False
    start_time = time.time()
    last_size = -1
    stable_count = 0
    while time.time() - start_time < timeout:
        if not os.path.exists(file_path):
            return False
        try:
            curr_size = os.path.getsize(file_path)
            if curr_size > 0 and curr_size == last_size:
                stable_count += 1
                if stable_count >= 2:
                    with open(file_path, "rb") as f:
                        f.read(512)
                    return True
            else:
                stable_count = 0
            last_size = curr_size
        except (OSError, PermissionError):
            stable_count = 0
        time.sleep(check_interval)
    return False


def parse_filename(filename, file_path=None):
    filename = os.path.basename(filename)
    extension = os.path.splitext(filename)[1].lstrip(".").lower()

    # 1. Проверяем паттерн входящего звонка (rg-...)
    m_in = PATTERN_INCOMING.match(filename)
    if m_in:
        p1, p2, date_str, time_str, unique_id, _ = m_in.groups()
        if len(p1) <= 4 and len(p2) > 4:
            internal_ext, phone = p1, p2
        elif len(p2) <= 4 and len(p1) > 4:
            phone, internal_ext = p1, p2
        else:
            phone, internal_ext = p2, p1

        call_date = datetime.strptime(date_str, "%Y%m%d").date()
        call_time = datetime.strptime(time_str, "%H%M%S").time()
        ts_dt = datetime.fromtimestamp(float(unique_id), tz=timezone.utc)
        return {
            "phone": phone,
            "internal_ext": internal_ext,
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
        p1, p2, date_str, time_str, unique_id, _ = m_out.groups()
        phone, internal_ext = p1, p2
        call_date = datetime.strptime(date_str, "%Y%m%d").date()
        call_time = datetime.strptime(time_str, "%H%M%S").time()
        ts_dt = datetime.fromtimestamp(float(unique_id), tz=timezone.utc)
        return {
            "phone": phone,
            "internal_ext": internal_ext,
            "call_date": call_date,
            "call_time": call_time,
            "unique_id": unique_id,
            "unix_timestamp": ts_dt,
            "extension": extension,
            "direction": "out"
        }

    # 3. Проверяем паттерн external-...
    m_ext = PATTERN_EXTERNAL.match(filename)
    if m_ext:
        internal_ext, phone, date_str, time_str, unique_id, _ = m_ext.groups()
        call_date = datetime.strptime(date_str, "%Y%m%d").date()
        call_time = datetime.strptime(time_str, "%H%M%S").time()
        ts_dt = datetime.fromtimestamp(float(unique_id), tz=timezone.utc)
        return {
            "phone": phone,
            "internal_ext": internal_ext,
            "call_date": call_date,
            "call_time": call_time,
            "unique_id": unique_id,
            "unix_timestamp": ts_dt,
            "extension": extension,
            "direction": "in"
        }

    # 4. Проверяем общий паттерн
    m_gen = PATTERN_GENERAL.match(filename)
    if m_gen:
        phone, date_str, time_str, unique_id, _ = m_gen.groups()
        call_date = datetime.strptime(date_str, "%Y%m%d").date()
        call_time = datetime.strptime(time_str, "%H%M%S").time()
        ts_dt = datetime.fromtimestamp(float(unique_id), tz=timezone.utc)
        m_ext_num = re.search(r'\b(100[1-8])\b', filename)
        internal_ext = m_ext_num.group(1) if m_ext_num else None
        return {
            "phone": phone,
            "internal_ext": internal_ext,
            "call_date": call_date,
            "call_time": call_time,
            "unique_id": unique_id,
            "unix_timestamp": ts_dt,
            "extension": extension,
            "direction": "out" if filename.lower().startswith("out") else "in"
        }

    # 5. Универсальный fallback для любых аудиофайлов
    phone_match = re.search(r"\b(\d{10,12})\b", filename)
    phone = phone_match.group(1) if phone_match else "Клиент"

    m_ext_num = re.search(r'\b(100[1-8])\b', filename)
    internal_ext = m_ext_num.group(1) if m_ext_num else None

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
        "internal_ext": internal_ext,
        "call_date": call_date,
        "call_time": call_time,
        "unique_id": unique_id,
        "unix_timestamp": ts_dt,
        "extension": extension,
        "direction": "out" if filename.lower().startswith("out") else "in"
    }


def detect_manager_from_text(text):
    """Определение имени менеджера из начала диалога.
    Услышанное имя приводится к реальному сотруднику из managers.json (см. manager_registry.py);
    искажённые или неизвестные имена НЕ создают «нового менеджера» — вернётся «Оператор»."""
    from manager_registry import resolve_or_warn, find_in_text
    if not text:
        return "Оператор"
    candidates = []
    m = re.search(r'[«"]?[Ээ]йкос[»"]?\s*[,–-]?\s*([А-ЯЁӘІҢҒҮҰҚӨҺ][а-яёәіңғүұқөһ]{2,})', text)
    if m and m.group(1).lower() not in ["звоню", "слушаю", "добрый", "здравствуйте", "день", "компания"]:
        candidates.append(m.group(1))
    m2 = re.search(r'меня\s+([А-ЯЁӘІҢҒҮҰҚӨҺ][а-яёәіңғүұқөһ]{2,})\s+зовут', text, re.I)
    if m2:
        candidates.append(m2.group(1))
    m3 = re.search(r'меня\s+зовут\s+([А-ЯЁӘІҢҒҮҰҚӨҺ][а-яёәіңғүұқөһ]{2,})', text, re.I)
    if m3:
        candidates.append(m3.group(1))
    m4 = re.search(r'оператор\s+([А-ЯЁӘІҢҒҮҰҚӨҺ][а-яёәіңғүұқөһ]{2,})', text[:300], re.I)
    if m4:
        candidates.append(m4.group(1))
    for cand in candidates:
        canon = resolve_or_warn(cand, "приветствие в записи")
        if canon:
            return canon
    return find_in_text(text) or "Оператор"


# Нарастающая пауза повторов при ошибках: 1 минута -> 5 минут -> 15 минут
RETRY_DELAYS = [60, 300, 900]


def get_call_record(unique_id, filename=None):
    """Возвращает информацию о звонке из БД для проверки статуса очереди."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        if filename:
            cur.execute("""
                SELECT id, status, retry_count, last_error, text, processing_started_at
                FROM call_transcriptions
                WHERE unique_id = %s OR source_file = %s;
            """, (unique_id, filename))
        else:
            cur.execute("""
                SELECT id, status, retry_count, last_error, text, processing_started_at
                FROM call_transcriptions
                WHERE unique_id = %s;
            """, (unique_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "status": row[1],
            "retry_count": row[2] or 0,
            "last_error": row[3],
            "text": row[4],
            "processing_started_at": row[5]
        }
    except Exception as e:
        logging.warning(f"Ошибка чтения статуса звонка из БД: {e}")
        return None


def mark_processing_start(data, filename):
    """Фиксирует начало обработки звонка в очереди БД."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    query = """
        INSERT INTO call_transcriptions (
            phone, call_date, call_time, unique_id, unix_timestamp,
            source_file, status, retry_count, processing_started_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'processing', 1, NOW())
        ON CONFLICT (unique_id) DO UPDATE SET
            status = 'processing',
            source_file = EXCLUDED.source_file,
            retry_count = COALESCE(call_transcriptions.retry_count, 0) + 1,
            processing_started_at = NOW()
        RETURNING id, retry_count;
    """
    cur.execute(query, (
        data["phone"],
        data["call_date"],
        data["call_time"],
        data["unique_id"],
        data["unix_timestamp"],
        filename
    ))
    res = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return res[0], res[1]


def mark_processing_completed(call_id, text, manager_name):
    """Обновляет статус звонка в БД на completed."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        UPDATE call_transcriptions
        SET status = 'completed',
            text = %s,
            manager_name = %s,
            last_error = NULL
        WHERE id = %s;
    """, (text, manager_name, call_id))
    conn.commit()
    cur.close()
    conn.close()


def mark_processing_failed(unique_id, error_msg, max_retries=MAX_RETRIES):
    """Фиксирует сбой обработки, инкрементирует ошибку и выставляет retry_pending или failed."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        UPDATE call_transcriptions
        SET status = CASE WHEN retry_count >= %s THEN 'failed' ELSE 'retry_pending' END,
            last_error = %s
        WHERE unique_id = %s
        RETURNING retry_count, status;
    """, (max_retries, str(error_msg)[:1000], unique_id))
    res = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    if res:
        return res[0], res[1]
    return 1, 'failed'


def get_gemini_api_keys():
    from dotenv import load_dotenv
    # override=True позволяет подхватывать добавленные ключи на лету из .env без перезапуска процесса
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        load_dotenv(override=True)
    
    keys = []
    
    keys_raw = os.environ.get("GEMINI_API_KEYS", "")
    for k in re.split(r"[,;\n]+", keys_raw):
        k = k.strip().strip('"').strip("'")
        if k and k not in keys:
            keys.append(k)
    
    single_key = os.environ.get("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    if single_key and single_key not in keys:
        keys.append(single_key)
            
    # Динамический поиск всех переменных GEMINI_API_KEY_<число> (1, 2, 3, 4, 5, 6, 7...)
    numbered_keys = []
    pattern = re.compile(r"^GEMINI_API_KEY_(\d+)$")
    for var_name, var_value in os.environ.items():
        m = pattern.match(var_name)
        if m:
            num = int(m.group(1))
            val = var_value.strip().strip('"').strip("'")
            if val:
                numbered_keys.append((num, val))
    
    # Сортируем по номеру ключа (GEMINI_API_KEY_1, GEMINI_API_KEY_2, ...)
    numbered_keys.sort(key=lambda x: x[0])
    for _, k in numbered_keys:
        if k not in keys:
            keys.append(k)

    return keys


_current_gemini_key_idx = 0

def get_current_gemini_key():
    global _current_gemini_key_idx
    keys = get_gemini_api_keys()
    if not keys:
        raise ValueError("API-ключ Google Gemini не найден в .env (укажите GEMINI_API_KEY, GEMINI_API_KEY_1... или GEMINI_API_KEYS)")
    return keys[_current_gemini_key_idx % len(keys)]

def rotate_gemini_key():
    global _current_gemini_key_idx
    keys = get_gemini_api_keys()
    if len(keys) <= 1:
        return None
    _current_gemini_key_idx = (_current_gemini_key_idx + 1) % len(keys)
    next_key = keys[_current_gemini_key_idx]
    masked = next_key[:6] + "..." + next_key[-4:] if len(next_key) > 10 else "***"
    logging.info(f"Смена API-ключа Gemini на ключ №{_current_gemini_key_idx + 1}/{len(keys)} ({masked})")
    return next_key


def transcribe_with_gemini(file_path, output_txt_path=None):
    from google import genai

    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Файл не найден: {abs_path}")

    filename = os.path.basename(abs_path)
    
    temp_upload_path = None
    try:
        filename.encode('ascii')
        upload_path = abs_path
    except UnicodeEncodeError:
        import tempfile
        ext = os.path.splitext(filename)[1] or ".wav"
        temp_file = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        temp_upload_path = temp_file.name
        temp_file.close()
        shutil.copy2(abs_path, temp_upload_path)
        upload_path = temp_upload_path

    prompt = (
        "Сделай точную построчную транскрипцию этого телефонного разговора. "
        "Язык разговора может быть казахским, русским или смешанным (казахский + русский). "
        "Обязательно раздели диалог на реплики Менеджера и Клиента:\n"
        "**Менеджер:** <реплика>\n"
        "**Клиент:** <реплика>\n"
        "Пиши точно на том языке, на котором говорят участники (казахский/русский), не переводи и не добавляй вводных слов от себя, верни только диалог."
    )

    models_to_try = get_configured_models()
    text = None

    keys = get_gemini_api_keys()
    max_key_attempts = max(1, len(keys))
    key_attempt = 0

    while key_attempt < max_key_attempts:
        keys = get_gemini_api_keys()
        max_key_attempts = max(max_key_attempts, len(keys))
        current_key = get_current_gemini_key()
        client = genai.Client(api_key=current_key)
        audio_file = None

        try:
            logging.info(f"Загрузка аудиофайла {filename} на серверы Google Gemini (Ключ #{_current_gemini_key_idx + 1}/{len(keys)})...")
            audio_file = client.files.upload(file=upload_path)
            logging.info(f"Файл {filename} загружен в Gemini API. Имя объекта: {audio_file.name}")

            for model_name in models_to_try:
                for retry in range(4):
                    try:
                        logging.info(f"Вызов Gemini API ({model_name}) для транскрибации (попытка {retry + 1}/4)...")
                        response = client.models.generate_content(
                            model=model_name,
                            contents=[audio_file, prompt]
                        )
                        text = response.text.strip() if response and response.text else None
                        if text:
                            logging.info(f"Успешный ответ от модели {model_name}.")
                            break
                    except Exception as e:
                        err_msg = str(e)
                        logging.warning(f"Ошибка при вызове {model_name} (попытка {retry + 1}/4): {err_msg[:120]}")
                        if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                            raise e
                        if "503" in err_msg or "UNAVAILABLE" in err_msg or "500" in err_msg or "504" in err_msg:
                            time.sleep(3)
                            continue
                        break
                if text:
                    break

            if text:
                break

        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                logging.warning(f"Лимит квоты (429) исчерпан на ключе #{_current_gemini_key_idx + 1}/{len(keys)}.")
                # Обновляем список ключей из .env на случай, если пользователь только что добавил новый ключ
                keys = get_gemini_api_keys()
                max_key_attempts = max(max_key_attempts, len(keys))
                if len(keys) > 1 and key_attempt < max_key_attempts - 1:
                    rotate_gemini_key()
                    time.sleep(1)
                    key_attempt += 1
                    continue
                else:
                    logging.error("Все доступные ключи Gemini исчерпали лимит (429).")
                    raise e
            else:
                logging.error(f"Непредвиденная ошибка Gemini API: {err_msg[:120]}")
                raise e

        finally:
            if audio_file:
                try:
                    client.files.delete(name=audio_file.name)
                except Exception:
                    pass

    if temp_upload_path and os.path.exists(temp_upload_path):
        try:
            os.remove(temp_upload_path)
        except Exception:
            pass

    if not text:
        raise RuntimeError("Не удалось получить транскрипцию от Gemini API.")

    if output_txt_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_txt_path)), exist_ok=True)
        with open(output_txt_path, "w", encoding="utf-8") as f:
            f.write(text)

    return text


PROMPT_UNIFIED_ANALYSIS = """Ты — профессиональный эксперт по контролю качества телефонных продаж и анализу клиентского сервиса компании «Эйкос» (сеть шинных центров и автосервисов в Казахстане).
Внимательно прослушай аудиозапись телефонного разговора и выполни комплексный глубокий анализ.
Язык звонка: русский, казахский или смешанный (суржик / переключение кодов).

Верни СТРОГИЙ валидный JSON без лишнего форматирования и без markdown-оберток следующей структуры:
{
  "dialogue": [
    {
      "speaker": "Менеджер" или "Клиент",
      "start_sec": 0.0,
      "end_sec": 2.5,
      "text": "точный текст реплики на оригинальном языке (RU/KK)",
      "sentiment": "positive" | "neutral" | "negative",
      "emotion": "Нейтральный" | "Радость" | "Грусть" | "Гнев" | "Раздражение"
    }
  ],
  "acoustics": {
    "speech_tempo_wpm": 125,
    "silence_sec": 3.5,
    "manager_speech_sec": 45.0,
    "client_speech_sec": 35.0
  },
  "overall_sentiment": {
    "satisfaction_score": <целое число от 1 до 5 по факту решения вопроса клиента>,
    "manager_politeness": <целое число от 1 до 5, отражающее культуру речи и вежливость>,
    "customer_sentiment_start": "нейтральный" | "позитивный" | "раздраженный" | "негативный",
    "customer_sentiment_end": "удовлетворенный" | "нейтральный" | "недовольный",
    "conflict_detected": false,
    "conflict_reason": null,
    "tone_summary": "краткий вывод 1-2 предложения об эмоциональном фоне и тоне общения"
  },
  "commercial": {
    "sale_status": "Продажа состоялась" | "Бронь оформлена" | "Отказ от покупки" | "Консультация",
    "order_sum": "сумма сделки с ₸ или '0 ₸'",
    "has_upsell": false,
    "upsell_sum": "сумма допродажи с ₸ или '0 ₸'",
    "upsell_text": "",
    "service_booking": "Да" | "Нет",
    "service_date": "",
    "client_name": null,
    "car_model": null,
    "tire_brand": null,
    "tire_size": null,
    "tire_season": "Зимние шипованные" | "Зимние фрикционные (липучка)" | "Летние" | "Всесезонные" | null,
    "tire_count": null,
    "branch_address": null,
    "payment_method": null,
    "lost_reason": null,
    "call_summary": "краткое резюме сути обращения и договоренностей",
    "call_category": "Подбор и бронь шин",
    "call_subcategory": "Заказ и бронирование",
    "call_tags": ["Шины"],
    "city_name": null
  },
  "checklist": [
    {
      "id": 1,
      "name": "1. Приветствие по стандарту компании",
      "title": "Приветствие",
      "status": "passed" | "failed" | "not_applicable",
      "passed": <true/false/null>,
      "score": <100 если passed, 0 если failed, null если not_applicable>,
      "quote": "точная цитата приветствия менеджера с именем и компанией",
      "comment": "пояснение"
    },
    {
      "id": 2,
      "name": "2. Выявление потребностей",
      "title": "Выявления потребностей",
      "status": "passed" | "failed",
      "passed": <true/false>,
      "score": <100 если passed, 0 если failed>,
      "quote": "точная цитата вопросов менеджера по авто/типоразмеру",
      "comment": "пояснение"
    },
    {
      "id": 3,
      "name": "3. Презентация товара / ценности",
      "title": "Презентация",
      "status": "passed" | "failed" | "not_applicable",
      "passed": <true/false/null>,
      "score": <100 если passed, 0 если failed, null если not_applicable>,
      "quote": "точная цитата презентации вариантов шин и цен либо 'Не применимо'",
      "comment": "пояснение"
    },
    {
      "id": 4,
      "name": "4. Отработка возражений / вопросов",
      "title": "Работа с возражениями",
      "status": "passed" | "failed",
      "passed": <true/false>,
      "score": <100 если passed, 0 если failed>,
      "quote": "точная цитата ответа на сомнения или вопросы наличия/претензии",
      "comment": "пояснение"
    },
    {
      "id": 5,
      "name": "5. Дополнительная продажа (Upsell)",
      "title": "Доп прдажа",
      "status": "passed" | "failed" | "not_applicable",
      "passed": <true/false/null>,
      "score": <100 если passed, 0 если failed, null если not_applicable>,
      "quote": "точная цитата предложения допуслуг либо 'Не предложено' / 'Не применимо'",
      "comment": "пояснение"
    },
    {
      "id": 6,
      "name": "6. Профессиональный подход",
      "title": "Профессиональный подход",
      "status": "passed" | "failed",
      "passed": <true/false>,
      "score": <100 если passed, 0 если failed>,
      "quote": "точная цитата вежливой фразы менеджера, отражающей культуру речи",
      "comment": "пояснение"
    },
    {
      "id": 7,
      "name": "7. Окончание разговора и фиксация",
      "title": "Окончание разговора",
      "status": "passed" | "failed",
      "passed": <true/false>,
      "score": <100 если passed, 0 если failed>,
      "quote": "точная цитата договоренности и вежливого прощания",
      "comment": "пояснение"
    }
  ]
}

ВАЖНЫЕ ПРАВИЛА:
1. ПОЛНАЯ ДОСЛОВНАЯ ТРАНСКРИПЦИЯ (dialogue):
   - Расшифруй аудиозапись ПОЛНОСТЬЮ И ДОСЛОВНО от первой до последней секунды!
   - НЕ сокращай, НЕ пересказывай тезисно и НЕ пропускай фразы! Все реплики менеджера и клиента должны быть включены в массив "dialogue".
   - Для каждой реплики укажи реального говорящего ("Менеджер" или "Клиент"), секунды начала/конца (start_sec, end_sec) и оригинальный текст.
2. НИКАКИХ выдуманных дефолтов! Если город не был назван — строго null. Если сезон не назван — строго null. Если клиент сказал «нешипованные» или «липучка» — это «Зимние фрикционные (липучка)».
3. Оценки CSAT (целое число от 1 до 5) и вежливости менеджера (целое число от 1 до 5, где 5 — безупречная вежливость, 1 — грубость) должны быть ЧЕСТНЫМИ и ОБЪЕКТИВНЫМИ по факту разговора. НИКАКИХ фиксированных 4 и 5 по умолчанию!
4. sale_status выбирается строго из 4 вариантов: "Продажа состоялась", "Бронь оформлена", "Отказ от покупки", "Консультация".
5. В чек-листе ОБЯЗАТЕЛЬНО указывать и "title", и "name", и "status" ("passed" | "failed" | "not_applicable"), и логическое поле "passed" (true | false | null), а также приводить РЕАЛЬНЫЕ цитаты («quote») из речи участников:
   - ПРИВЕТСТВИЕ И АРТЕФАКТЫ РАСПОЗНАВАНИЯ: из-за шума, тихой речи, плохой связи и казахского акцента имя менеджера и название компании «Эйкос» в записи часто искажаются (например, «Дельта компаниясы», «Айкос», чужие имена) или не слышны совсем. Проверяй только ФАКТ приветствия, а не то, какое имя или компания прозвучали. Если менеджер поздоровался и/или попытался представиться — этап «1. Приветствие» пройден ("status": "passed", "passed": true, "score": 100), даже если имя или компания не распознаны или распознаны неверно. В comment и quote этого этапа НИКОГДА не пиши, что менеджер назвал чужое/неверное имя или другую компанию. "failed" ставь только если менеджер вообще не поздоровался и никак не представился (например, ответил «Да?», «Слушаю»). Если это внутренний разговор сотрудников (коллега, филиал, партнёр, а не клиент) — ставь "status": "not_applicable", "passed": null, "score": null.
   - ВНИМАНИЕ ДЛЯ ЗВОНКОВ-ЖАЛОБ И СПЕЦИФИЧЕСКИХ ОБРАЩЕНИЙ: если звонок является претензией, жалобой или не связан с продажей товара, пункты «3. Презентация товара / ценности» и «5. Дополнительная продажа (Upsell)» являются неуместными. В таких случаях ОБЯЗАТЕЛЬНО ставь для них "status": "not_applicable", "passed": null, "score": null! Не штрафуй менеджера за отсутствие продажи в звонке по жалобе.
"""


def transcribe_and_analyze_with_gemini(file_path, output_txt_path=None):
    """
    Единый мультимодальный вызов Gemini API:
    Транскрибирует аудио со спикерами и таймкодами, определяет эмоции реплик,
    оценивает CSAT и скрипт продаж с цитатами, извлекает коммерческие сущности за один запрос.
    """
    from google import genai
    from google.genai import types

    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Файл не найден: {abs_path}")

    filename = os.path.basename(abs_path)
    
    temp_upload_path = None
    try:
        filename.encode('ascii')
        upload_path = abs_path
    except UnicodeEncodeError:
        import tempfile
        ext = os.path.splitext(filename)[1] or ".wav"
        temp_file = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        temp_upload_path = temp_file.name
        temp_file.close()
        shutil.copy2(abs_path, temp_upload_path)
        upload_path = temp_upload_path

    models_to_try = get_configured_models()
    result_data = None
    raw_response_text = None

    keys = get_gemini_api_keys()
    max_key_attempts = max(1, len(keys))
    key_attempt = 0

    while key_attempt < max_key_attempts:
        keys = get_gemini_api_keys()
        max_key_attempts = max(max_key_attempts, len(keys))
        current_key = get_current_gemini_key()
        client = genai.Client(api_key=current_key)
        audio_file = None

        try:
            logging.info(f"Загрузка аудиофайла {filename} для единого анализа Gemini (Ключ #{_current_gemini_key_idx + 1}/{len(keys)})...")
            audio_file = client.files.upload(file=upload_path)
            logging.info(f"Файл {filename} загружен в Gemini API. Имя объекта: {audio_file.name}")

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            )

            for model_name in models_to_try:
                for retry in range(4):
                    try:
                        logging.info(f"Вызов Gemini API ({model_name}) для единого анализа (попытка {retry + 1}/4)...")
                        response = client.models.generate_content(
                            model=model_name,
                            contents=[audio_file, PROMPT_UNIFIED_ANALYSIS],
                            config=config
                        )
                        raw_response_text = response.text.strip() if response and response.text else None
                        if raw_response_text:
                            try:
                                json_match = re.search(r"\{[\s\S]*\}", raw_response_text)
                                if json_match:
                                    result_data = json.loads(json_match.group(0))
                                else:
                                    result_data = json.loads(raw_response_text)
                            except Exception as pe:
                                logging.warning(f"Ошибка парсинга JSON от {model_name}: {pe}")
                                result_data = None

                            if result_data and isinstance(result_data, dict):
                                logging.info(f"Успешный структурированный ответ от модели {model_name}.")
                                break
                    except Exception as e:
                        err_msg = str(e)
                        logging.warning(f"Ошибка при вызове {model_name} (попытка {retry + 1}/4): {err_msg[:120]}")
                        if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                            raise e
                        if "503" in err_msg or "UNAVAILABLE" in err_msg or "500" in err_msg or "504" in err_msg:
                            time.sleep(3)
                            continue
                        break
                if result_data:
                    break

            if result_data:
                break

        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                logging.warning(f"Лимит квоты (429) исчерпан на ключе #{_current_gemini_key_idx + 1}/{len(keys)}.")
                # Обновляем список ключей из .env на случай добавления новых ключей
                keys = get_gemini_api_keys()
                max_key_attempts = max(max_key_attempts, len(keys))
                if len(keys) > 1 and key_attempt < max_key_attempts - 1:
                    rotate_gemini_key()
                    time.sleep(1)
                    key_attempt += 1
                    continue
                else:
                    logging.error("Все доступные ключи Gemini исчерпали лимит (429).")
                    raise e
            else:
                logging.error(f"Непредвиденная ошибка Gemini API: {err_msg[:120]}")
                raise e

        finally:
            if audio_file:
                try:
                    client.files.delete(name=audio_file.name)
                except Exception:
                    pass

    if temp_upload_path and os.path.exists(temp_upload_path):
        try:
            os.remove(temp_upload_path)
        except Exception:
            pass

    if not result_data:
        raise RuntimeError("Не удалось получить структурированный анализ от Gemini API.")

    # Формируем читаемый текст транскрипции
    dialogue = result_data.get("dialogue", [])
    if dialogue:
        lines = []
        for d in dialogue:
            spk = d.get("speaker", "Спикер")
            t = d.get("text", "")
            lines.append(f"**{spk}:** {t}")
        full_text = "\n\n".join(lines)
    else:
        full_text = raw_response_text or ""

    if output_txt_path and full_text:
        os.makedirs(os.path.dirname(os.path.abspath(output_txt_path)), exist_ok=True)
        with open(output_txt_path, "w", encoding="utf-8") as f:
            f.write(full_text)

    return result_data, full_text


def process_file(file_path):
    """
    Основной пайплайн надежной обработки файла:
    1. Защита от параллельной обработки одного и того же файла разными потоками
    2. Проверка полной записи файла (wait_for_file_ready)
    3. Проверка статуса очереди в PostgreSQL
    4. Перемещение в processing/
    5. Единый мультимодальный анализ через Gemini (транскрипция + тональность + чек-лист + коммерция)
    6. Определение менеджера по внутреннему номеру или тексту
    7. Сохранение всех уровней анализа в БД за одну операцию
    8. Безопасное перемещение в processed/ при успехе либо в error/ при превышении retry
    """
    filename = os.path.basename(file_path)

    # 1. Защита от гонки потоков
    with _processing_lock:
        if filename in _currently_processing_files:
            logging.info(f"Файл {filename} уже обрабатывается другим потоком. Пропускаем.")
            return
        _currently_processing_files.add(filename)

    try:
        logging.info(f"Начало обработки файла: {filename}")

        # Проверяем докопирование файла
        if not wait_for_file_ready(file_path):
            logging.warning(f"Файл {filename} еще не завершил запись или заблокирован. Пропускаем до следующего цикла.")
            return

        # Извлекаем метаданные имени файла
        data = parse_filename(filename, file_path=file_path)
        unique_id = data["unique_id"]

        # Проверяем статус в БД
        rec = get_call_record(unique_id, filename=filename)
        if rec and rec["status"] == "completed" and rec["text"] and rec["text"] != "ТЕСТОВАЯ ТРАНСКРИБАЦИЯ":
            logging.info(f"Файл {filename} уже успешно обработан ранее (status=completed). Перемещаем в processed/.")
            dest = os.path.join(PROCESSED_DIR, filename)
            if os.path.abspath(file_path) != os.path.abspath(dest) and os.path.exists(file_path):
                try:
                    if os.path.exists(dest):
                        os.remove(dest)
                    shutil.move(file_path, dest)
                except Exception as e:
                    logging.warning(f"Не удалось переместить {filename} в {PROCESSED_DIR}: {e}")
            return

        # Если файл был повторно помещен пользователем в incoming/, а ранее был failed - даем ему новый шанс
        is_in_incoming = os.path.dirname(os.path.abspath(file_path)).lower() == os.path.abspath(INCOMING_DIR).lower()
        if rec and rec["status"] == "failed" and is_in_incoming:
            logging.info(f"Файл {filename} был вручную возвращен в incoming/. Сбрасываем счетчик попыток с {rec['retry_count']} на 0.")
            try:
                conn_reset = psycopg2.connect(**DB_CONFIG)
                cur_reset = conn_reset.cursor()
                cur_reset.execute("""
                    UPDATE call_transcriptions
                    SET status = 'pending', retry_count = 0, last_error = NULL
                    WHERE unique_id = %s OR source_file = %s;
                """, (unique_id, filename))
                conn_reset.commit()
                cur_reset.close()
                conn_reset.close()
                rec["status"] = "pending"
                rec["retry_count"] = 0
            except Exception as e:
                logging.warning(f"Не удалось сбросить статус попыток в БД для {filename}: {e}")

        if rec and rec["status"] == "failed" and rec["retry_count"] >= MAX_RETRIES:
            logging.warning(f"Файл {filename} ранее превысил лимит попыток ({rec['retry_count']}/{MAX_RETRIES}). Перемещаем в error/.")
            dest = os.path.join(ERROR_DIR, filename)
            if os.path.abspath(file_path) != os.path.abspath(dest) and os.path.exists(file_path):
                try:
                    if os.path.exists(dest):
                        os.remove(dest)
                    shutil.move(file_path, dest)
                except Exception as e:
                    logging.warning(f"Не удалось переместить {filename} в {ERROR_DIR}: {e}")
            return

        # Проверка нарастающей паузы повтора (1 -> 5 -> 15 минут)
        if rec and rec["status"] == "retry_pending" and rec.get("retry_count", 0) > 0:
            cur_r = rec["retry_count"]
            delay_sec = RETRY_DELAYS[min(cur_r - 1, len(RETRY_DELAYS) - 1)]
            started_at = rec.get("processing_started_at")
            if started_at:
                now_utc = datetime.now(timezone.utc)
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                elapsed = (now_utc - started_at).total_seconds()
                if elapsed < delay_sec:
                    remaining = int(delay_sec - elapsed)
                    logging.info(f"Файл {filename} ожидает нарастающую паузу повтора (попытка {cur_r}/{MAX_RETRIES}, пауза {max(1, delay_sec // 60)} мин, осталось {remaining} сек). Пропускаем.")
                    return

        # Перемещаем файл в processing/
        processing_file = os.path.join(PROCESSING_DIR, filename)
        if os.path.abspath(file_path) != os.path.abspath(processing_file) and os.path.exists(file_path):
            try:
                shutil.move(file_path, processing_file)
            except Exception as e:
                logging.error(f"Не удалось переместить {filename} в {PROCESSING_DIR}: {e}")
                processing_file = file_path

        # Регистрируем начало обработки в БД
        inserted_id, cur_retry = mark_processing_start(data, filename)
        logging.info(f"Данные файла {filename}: Телефон={data['phone']}, Дата={data['call_date']}, Время={data['call_time']}, Unique ID={unique_id}, Попытка={cur_retry}/{MAX_RETRIES}")

        try:
            output_txt_name = os.path.splitext(filename)[0] + ".txt"
            output_txt_path = os.path.join(TRANSCRIPTIONS_DIR, output_txt_name)

            # Единый анализ через Gemini (или fallback на двухэтапный пайплайн)
            try:
                analysis_data, text = transcribe_and_analyze_with_gemini(processing_file, output_txt_path)
                logging.info(f"Единый анализ Gemini успешно завершен для {filename}.")

                # Определение менеджера: приоритет живому имени из приветствия
                manager_name = None
                if text:
                    manager_name = detect_manager_from_text(text)
                if not manager_name and data.get("internal_ext"):
                    manager_name = get_operator_by_extension(data["internal_ext"])

                # Сохранение всех уровней анализа в БД за одну операцию
                from ton_voice_processor import save_unified_analysis
                save_unified_analysis(inserted_id, processing_file, analysis_data, text, manager_name)

            except Exception as e_unified:
                logging.error(f"[-] Сбой анализа Gemini для {filename}: {e_unified}. Файл оставляем в очереди на повтор.")
                raise e_unified

            # Безопасный перенос в processed/
            processed_file = os.path.join(PROCESSED_DIR, filename)
            try:
                if os.path.exists(processing_file):
                    if os.path.abspath(processing_file) != os.path.abspath(processed_file):
                        if os.path.exists(processed_file):
                            os.remove(processed_file)
                        shutil.move(processing_file, processed_file)
                    logging.info(f"[+] Файл {filename} успешно обработан и перемещен в {PROCESSED_DIR}")
                elif os.path.exists(processed_file):
                    logging.info(f"[+] Файл {filename} уже находится в {PROCESSED_DIR}")
            except Exception as e:
                logging.warning(f"Не удалось переместить {filename} в {PROCESSED_DIR}: {e}")

            # Автоматический экспорт свежих данных для дашборда
            try:
                from export_db_to_json import export_calls_to_json
                export_calls_to_json(write_to_file=True)
            except Exception as ex:
                logging.warning(f"Фоновый экспорт в calls_data.json: {ex}")

            logging.info("=" * 60)
            logging.info(f"[✓ ГОТОВО] Файл {filename} полностью обработан!")
            logging.info(">>> Ожидание следующего файла в incoming/ (переместите новый файл)...")
            logging.info("=" * 60)

        except Exception as error:
            err_msg = str(error)
            logging.error(f"[-] Ошибка при обработке файла {filename}: {err_msg}")
            cur_retries, status = mark_processing_failed(unique_id, err_msg, max_retries=MAX_RETRIES)

            if cur_retries >= MAX_RETRIES:
                logging.error(f"[-] Лимит попыток исчерпан ({cur_retries}/{MAX_RETRIES}). Перемещение {filename} в {ERROR_DIR}")
                err_dest = os.path.join(ERROR_DIR, filename)
                try:
                    if os.path.exists(processing_file):
                        if os.path.exists(err_dest):
                            os.remove(err_dest)
                        shutil.move(processing_file, err_dest)
                except Exception as e:
                    logging.error(f"Не удалось переместить {filename} в {ERROR_DIR}: {e}")
            else:
                delay_sec = RETRY_DELAYS[min(cur_retries - 1, len(RETRY_DELAYS) - 1)]
                logging.info(f"[!] Файл {filename} возвращен в incoming/ со статусом {status}. Следующая попытка через {max(1, delay_sec // 60)} мин (пауза {delay_sec} сек).")
                inc_dest = os.path.join(INCOMING_DIR, filename)
                try:
                    if os.path.exists(processing_file):
                        if os.path.exists(inc_dest):
                            os.remove(inc_dest)
                        shutil.move(processing_file, inc_dest)
                except Exception as e:
                    logging.error(f"Не удалось переместить {filename} в {INCOMING_DIR}: {e}")

    finally:
        with _processing_lock:
            _currently_processing_files.discard(filename)


class MP3Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        ext = os.path.splitext(event.src_path)[1].lower()
        if ext in AUDIO_EXTENSIONS:
            process_file(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        ext = os.path.splitext(event.dest_path)[1].lower()
        if ext in AUDIO_EXTENSIONS:
            process_file(event.dest_path)


def scan_and_process_pending():
    """Сканирует processing/ и incoming/ на наличие файлов, ожидающих обработки."""
    # 1. Сначала проверяем processing/ на случай аварийного перезапуска
    for f in os.listdir(PROCESSING_DIR):
        ext = os.path.splitext(f)[1].lower()
        if ext in AUDIO_EXTENSIONS:
            full_p = os.path.join(PROCESSING_DIR, f)
            logging.info(f"Обнаружен незавершенный файл в {PROCESSING_DIR}: {f}. Возобновляем обработку...")
            process_file(full_p)

    # 2. Затем incoming/
    for f in os.listdir(INCOMING_DIR):
        ext = os.path.splitext(f)[1].lower()
        if ext in AUDIO_EXTENSIONS:
            full_p = os.path.join(INCOMING_DIR, f)
            process_file(full_p)


def return_errors_to_incoming():
    """Возвращает все файлы из error/ обратно в incoming/ и сбрасывает счетчики ошибок в БД."""
    logging.info("=== Возврат файлов из error/ в очередь incoming/ ===")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cnt = 0
    if os.path.exists(ERROR_DIR):
        for fname in os.listdir(ERROR_DIR):
            ext = os.path.splitext(fname)[1].lower()
            if ext in AUDIO_EXTENSIONS:
                src = os.path.join(ERROR_DIR, fname)
                dst = os.path.join(INCOMING_DIR, fname)
                try:
                    if os.path.exists(dst):
                        os.remove(dst)
                    shutil.move(src, dst)
                    parsed = parse_filename(fname, file_path=src)
                    uid = parsed.get("unique_id", "")
                    cur.execute("""
                        UPDATE call_transcriptions
                        SET status = 'new', retry_count = 0, last_error = NULL
                        WHERE source_file = %s OR unique_id = %s;
                    """, (fname, uid))
                    cnt += 1
                    logging.info(f"[+] Файл {fname} возвращен из error/ в incoming/ (счетчик попыток сброшен).")
                except Exception as ex:
                    logging.error(f"[-] Ошибка возврата {fname}: {ex}")
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"=== Завершено: {cnt} файлов возвращено в incoming/ ===")
    return cnt


def main():
    if "--retry-errors" in sys.argv:
        return_errors_to_incoming()
        return

    logging.info("Запуск COMPANY AI - CALL WATCHER (Надежная очередь, ротация ключей, повторы)")

    for d in [INCOMING_DIR, PROCESSING_DIR, PROCESSED_DIR, ERROR_DIR, TRANSCRIPTIONS_DIR]:
        os.makedirs(d, exist_ok=True)

    logging.info(f"Директории: incoming={INCOMING_DIR}, processing={PROCESSING_DIR}, processed={PROCESSED_DIR}, error={ERROR_DIR}")
    logging.info(f"Модели Gemini: {get_configured_models()}")
    logging.info(f"Лимит попыток (MAX_RETRIES): {MAX_RETRIES}, таймаут готовности файла: {FILE_READY_TIMEOUT} сек.")

    # Первичная проверка очереди
    try:
        scan_and_process_pending()
    except Exception as e:
        logging.error(f"Ошибка при первичной проверке файлов очереди: {e}")

    # Запуск файлового наблюдателя
    event_handler = MP3Handler()
    observer = Observer()
    observer.schedule(event_handler, INCOMING_DIR, recursive=False)
    observer.start()

    logging.info("Ожидание новых аудиозаписей в incoming/...")

    try:
        last_scan_time = time.time()
        while True:
            time.sleep(1)
            # Каждые 60 секунд проверяем очередь на наличие отложенных повторов
            if time.time() - last_scan_time > 60:
                last_scan_time = time.time()
                try:
                    scan_and_process_pending()
                except Exception as ex:
                    logging.warning(f"Ошибка фонового сканирования очереди: {ex}")
    except KeyboardInterrupt:
        logging.info("Остановка службы...")
        observer.stop()

    observer.join()
    logging.info("Служба воркера остановлена.")


if __name__ == "__main__":
    main()