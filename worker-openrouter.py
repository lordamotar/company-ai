import os
import re
import time
import shutil
import logging
import sys
import json
import base64
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import psycopg2
import threading
import httpx
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
        logging.FileHandler(os.path.join(BASE_DIR, "worker-openrouter.log"), encoding="utf-8"),
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

# Список старших моделей OpenRouter по приоритету (только Pro и 3.x+ Flash)
DEFAULT_OPENROUTER_MODELS = [
    "google/gemini-3.7-flash",
    "google/gemini-2.5-pro",
    "google/gemini-3.8-flash",
    "google/gemini-2.5-flash"
]

def get_configured_models():
    """Возвращает список моделей OpenRouter из окружения или дефолтный список."""
    env_val = os.getenv("OPENROUTER_MODELS", "") or os.getenv("OPENROUTER_MODEL", "")
    if env_val:
        models = [m.strip() for m in env_val.split(",") if m.strip()]
        if models:
            return models
    return DEFAULT_OPENROUTER_MODELS

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
PATTERN_INCOMING = re.compile(
    r"^(?:rg|in|incoming)-(\d+)-(\d+)-(\d{8})-(\d{6})-(\d+\.\d+)\.([a-z0-9]+)$",
    re.IGNORECASE
)
PATTERN_OUTGOING = re.compile(
    r"^(?:out|outgoing)-(\d+)-(\d+)-(\d{8})-(\d{6})-(\d+\.\d+)\.([a-z0-9]+)$",
    re.IGNORECASE
)
PATTERN_EXTERNAL = re.compile(
    r"^(?:external)-(\d+)-(\d+)-(\d{8})-(\d{6})-(\d+\.\d+)\.([a-z0-9]+)$",
    re.IGNORECASE
)
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
    # «оператор Диас», по-казахски «операторыңыз Нұржан»
    m4 = re.search(r'оператор[а-яәіңғүұқөһ]*\s+([А-ЯЁӘІҢҒҮҰҚӨҺ][а-яёәіңғүұқөһ]{2,})', text[:300], re.I)
    if m4:
        candidates.append(m4.group(1))
    # по-казахски «менің атым Нұржан» / «атым Нұржан»
    m5 = re.search(r'\bатым\s+([А-ЯЁӘІҢҒҮҰҚӨҺ][а-яёәіңғүұқөһ]{2,})', text[:300], re.I)
    if m5:
        candidates.append(m5.group(1))
    for cand in candidates:
        canon = resolve_or_warn(cand, "приветствие в записи")
        if canon:
            return canon
    return find_in_text(text) or "Оператор"


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


def get_openrouter_api_key():
    from dotenv import load_dotenv
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        load_dotenv(override=True)
    
    key = os.environ.get("OPENROUTER_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        raise ValueError("API-ключ OpenRouter не найден в .env (укажите OPENROUTER_API_KEY)")
    return key


# Промпт единого анализа звонка — общий для всех воркеров: prompts/unified_analysis_prompt.md
# (справочник категорий подставляется из call_categories.py)
from prompt_loader import load_prompt
PROMPT_UNIFIED_ANALYSIS = load_prompt("unified_analysis_prompt")


def get_audio_format_for_openrouter(file_path):
    """Определяет формат аудиофайла для OpenRouter input_audio."""
    ext = os.path.splitext(file_path)[1].lstrip(".").lower()
    if ext in ["wav", "wave"]:
        return "wav"
    if ext == "mp3":
        return "mp3"
    if ext == "ogg":
        return "ogg"
    if ext == "m4a":
        return "m4a"
    if ext == "flac":
        return "flac"
    if ext == "aac":
        return "aac"
    return "wav"


def transcribe_and_analyze_with_openrouter(file_path, output_txt_path=None, meta_out=None):
    """
    Единый мультимодальный вызов OpenRouter API:
    Передает аудиофайл (base64) в совместимую модель Gemini на OpenRouter,
    получает дословную транскрипцию со спикерами и полный аудит звонка.
    """
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Файл не найден: {abs_path}")

    filename = os.path.basename(abs_path)
    api_key = get_openrouter_api_key()
    models_to_try = get_configured_models()

    logging.info(f"Кодирование аудио {filename} в base64 для передачи через OpenRouter...")
    with open(abs_path, "rb") as af:
        audio_bytes = af.read()
    base64_audio = base64.b64encode(audio_bytes).decode("utf-8")
    audio_format = get_audio_format_for_openrouter(abs_path)
    logging.info(f"Аудио {filename} закодировано ({len(audio_bytes) / 1024:.1f} КБ, формат: {audio_format}).")

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://aikos.kz",
        "X-Title": "Aikos Call AI Analyzer"
    }

    result_data = None
    raw_response_text = None
    used_model = None

    # Таймаут увеличен до 180 секунд для больших аудио
    with httpx.Client(timeout=180.0) as http_client:
        for model_name in models_to_try:
            for retry in range(4):
                try:
                    logging.info(f"Вызов OpenRouter API ({model_name}) для анализа {filename} (попытка {retry + 1}/4)...")
                    
                    payload = {
                        "model": model_name,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": PROMPT_UNIFIED_ANALYSIS
                                    },
                                    {
                                        "type": "input_audio",
                                        "input_audio": {
                                            "data": base64_audio,
                                            "format": audio_format
                                        }
                                    }
                                ]
                            }
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2
                    }

                    resp = http_client.post(url, headers=headers, json=payload)
                    
                    if resp.status_code == 400 and "response_format" in resp.text:
                        # Некоторые провайдеры OpenRouter не поддерживают строгий response_format
                        logging.info("Повтор запроса без параметра response_format...")
                        payload.pop("response_format", None)
                        resp = http_client.post(url, headers=headers, json=payload)

                    if resp.status_code != 200:
                        err_text = resp.text
                        logging.warning(f"Ошибка OpenRouter ({model_name}) HTTP {resp.status_code}: {err_text[:200]}")
                        
                        # 402 - недостаточно средств на балансе OpenRouter
                        if resp.status_code == 402:
                            raise RuntimeError(f"OpenRouter 402 Insufficient Credits: {err_text}")
                        # 401 - неверный ключ
                        if resp.status_code == 401:
                            raise RuntimeError(f"OpenRouter 401 Unauthorized (проверьте OPENROUTER_API_KEY): {err_text}")
                        # 429 или 5xx - пробуем повторить с паузой
                        if resp.status_code in [429, 500, 502, 503, 504]:
                            time.sleep(4)
                            continue
                        break

                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        raw_response_text = msg.get("content", "").strip()

                    if raw_response_text:
                        try:
                            json_match = re.search(r"\{[\s\S]*\}", raw_response_text)
                            if json_match:
                                result_data = json.loads(json_match.group(0))
                            else:
                                result_data = json.loads(raw_response_text)
                        except Exception as pe:
                            logging.warning(f"Ошибка парсинга JSON от OpenRouter ({model_name}): {pe}")
                            result_data = None

                        if result_data and isinstance(result_data, dict):
                            logging.info(f"Успешный структурированный ответ от OpenRouter ({model_name}).")
                            used_model = model_name
                            break
                            
                except httpx.RequestError as exc:
                    logging.warning(f"Сетевая ошибка HTTP при запросе к OpenRouter ({model_name}): {exc}")
                    time.sleep(3)
                    continue
                except RuntimeError as re_err:
                    raise re_err
                except Exception as e:
                    logging.warning(f"Исключение при вызове OpenRouter ({model_name}): {e}")
                    time.sleep(3)
                    continue

            if result_data:
                break

    if not result_data:
        raise RuntimeError("Не удалось получить структурированный анализ от OpenRouter API.")

    if meta_out is not None:
        from analysis_runs import prompt_version
        meta_out.update({
            "provider": "openrouter",
            "model": used_model,
            "prompt_version": prompt_version(PROMPT_UNIFIED_ANALYSIS),
        })

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
    Основной пайплайн обработки файла через OpenRouter:
    1. Защита от параллельной обработки одного и того же файла разными потоками
    2. Проверка полной записи файла (wait_for_file_ready)
    3. Проверка статуса очереди в PostgreSQL
    4. Перемещение в processing/
    5. Единый мультимодальный анализ через OpenRouter (транскрипция + тональность + чек-лист + коммерция)
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

            # Единый анализ через OpenRouter
            try:
                analysis_meta = {}
                t_start = time.time()
                analysis_data, text = transcribe_and_analyze_with_openrouter(processing_file, output_txt_path, meta_out=analysis_meta)
                duration_ms = int((time.time() - t_start) * 1000)
                logging.info(f"Единый анализ OpenRouter успешно завершен для {filename}.")

                # Определение менеджера: приоритет живому имени из приветствия
                manager_name = None
                if text:
                    manager_name = detect_manager_from_text(text)
                if not manager_name and data.get("internal_ext"):
                    manager_name = get_operator_by_extension(data["internal_ext"])

                # Сохранение всех уровней анализа в БД за одну операцию
                from ton_voice_processor import save_unified_analysis
                save_unified_analysis(inserted_id, processing_file, analysis_data, text, manager_name)

                # Фиксируем, какой моделью и каким промптом получен анализ (для сравнения на эталоне)
                from analysis_runs import record_run
                record_run(
                    unique_id, analysis_meta.get("provider", "openrouter"), analysis_meta.get("model"),
                    analysis_meta.get("prompt_version"), analysis_data,
                    call_id=inserted_id, duration_ms=duration_ms,
                )

            except Exception as e_unified:
                logging.error(f"[-] Сбой анализа OpenRouter для {filename}: {e_unified}. Файл оставляем в очереди на повтор.")
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
    if os.path.exists(PROCESSING_DIR):
        for f in os.listdir(PROCESSING_DIR):
            ext = os.path.splitext(f)[1].lower()
            if ext in AUDIO_EXTENSIONS:
                full_p = os.path.join(PROCESSING_DIR, f)
                logging.info(f"Обнаружен незавершенный файл в {PROCESSING_DIR}: {f}. Возобновляем обработку...")
                process_file(full_p)

    if os.path.exists(INCOMING_DIR):
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

    logging.info("Запуск COMPANY AI - CALL WATCHER via OPENROUTER (Надежная очередь, повторы)")

    for d in [INCOMING_DIR, PROCESSING_DIR, PROCESSED_DIR, ERROR_DIR, TRANSCRIPTIONS_DIR]:
        os.makedirs(d, exist_ok=True)

    logging.info(f"Директории: incoming={INCOMING_DIR}, processing={PROCESSING_DIR}, processed={PROCESSED_DIR}, error={ERROR_DIR}")
    logging.info(f"Модели OpenRouter: {get_configured_models()}")
    logging.info(f"Лимит попыток (MAX_RETRIES): {MAX_RETRIES}, таймаут готовности файла: {FILE_READY_TIMEOUT} сек.")

    # Проверка ключа
    try:
        key = get_openrouter_api_key()
        masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
        logging.info(f"OpenRouter API ключ загружен: {masked}")
    except Exception as e:
        logging.error(f"Ошибка проверки ключа OpenRouter: {e}")
        return

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
    logging.info("Служба воркера OpenRouter остановлена.")


if __name__ == "__main__":
    main()
