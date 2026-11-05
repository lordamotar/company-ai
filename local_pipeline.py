# -*- coding: utf-8 -*-
"""
local_pipeline.py — локальный анализ звонка без облака: ASR (faster-whisper) + LLM (Ollama).

Облако делает всё одним вызовом «аудио -> JSON». Здесь это два шага:
  1. transcribe()  — реплики с таймкодами;
  2. analyze_transcript() — по тексту LLM возвращает спикеров, тональность реплик,
     оценки, коммерцию и чек-лист.
Результат имеет ту же JSON-схему, что и у Gemini, поэтому save_unified_analysis,
база и веб-интерфейс не меняются.

Запись моно, поэтому спикеров размечает LLM по смыслу реплик. Это слабое место схемы:
если в один сегмент ASR попали оба собеседника, разделить их уже нельзя.
Стерео-запись в Asterisk снимет проблему (docs/LOCAL_SERVER.md).

Настройки (.env):
  ASR_MODEL          модель faster-whisper или путь к сконвертированной (large-v3-turbo)
  ASR_DEVICE         cuda | cpu (cuda)
  ASR_COMPUTE_TYPE   int8 | int8_float16 | float16 (int8). GTX 1050 умеет только int8 и float32:
                     float16 требует карту новее; на сервере ставить int8_float16 или float16
  ASR_LANGUAGE       пусто — определять автоматически; ru | kk — зафиксировать
  ASR_MODEL_KK       модель для казахских звонков (test/models/kazakh-turbo-int8)
  KAZAKH_WORD_SHARE  звонок казахский, если доля слов с казахскими буквами не ниже этой (0.15)
  KAZAKH_LANG_PROB   или если вероятность казахского по Whisper не ниже этой (0.02)
  ASR_BEAM_SIZE      (5)
  OLLAMA_URL         (http://127.0.0.1:11434)
  OLLAMA_MODEL       (qwen3:4b)
  OLLAMA_MODEL_KK    LLM для казахских звонков; пусто — та же, что OLLAMA_MODEL
  OLLAMA_NUM_CTX     окно контекста в токенах (16384)
  OLLAMA_NUM_PREDICT предел длины ответа в токенах (6000)
  OLLAMA_TIMEOUT     секунд ожидания ответа Ollama без единого токена (1800)
  OLLAMA_MIN_TOKENS_PER_SEC  если скорость ответа ниже, анализ прерывается (1.0; 0 — не проверять)
  LOW_VRAM           true — ASR и LLM выгружаются после каждого шага (для карт меньше 12 ГБ)
"""

import gc
import json
import logging
import os
import re
import threading
import time

import requests
from dotenv import load_dotenv

from prompt_loader import load_prompt

load_dotenv()

# На Windows CTranslate2 ищет cublas64_12.dll через PATH; пакет nvidia-cublas-cu12 кладёт её в site-packages
if os.name == "nt":
    import site
    for _site_dir in site.getsitepackages():
        _cublas_dir = os.path.join(_site_dir, "nvidia", "cublas", "bin")
        if os.path.isdir(_cublas_dir):
            os.environ["PATH"] = _cublas_dir + os.pathsep + os.environ.get("PATH", "")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "6000"))
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "1800"))
MIN_TOKENS_PER_SEC = float(os.getenv("OLLAMA_MIN_TOKENS_PER_SEC", "1.0"))
SPEED_CHECK_SEC = 60   # скорость считается со времени первого токена, проверка начинается после этого срока
LLM_ATTEMPTS = 2


class SlowGenerationError(RuntimeError):
    """Модель пишет ответ медленнее допустимого: за разумное время он не будет готов."""

# Слова, которые обычный Whisper чаще всего искажает в звонках компании
ASR_HOTWORDS = "Эйкос, Aikos, шиномонтаж, типоразмер, липучка, шипованные, балансировка, БИН, ТОО"

SALE_STATUSES = ["Продажа состоялась", "Бронь оформлена", "Отказ от покупки", "Консультация"]
CHECKLIST_SIZE = 7

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KAZAKH_LETTERS = set("әғқңөұүһі")
# Казахская модель не ставит таймкоды сама: реплики собираются из слов по паузам
PHRASE_PAUSE_SEC = 0.6
# Whisper иногда перескакивает через кусок речи (детектор речи его слышит, а текста нет).
# Такие куски длиннее GAP_MIN_SEC расшифровываются заново отдельно.
GAP_MIN_SEC = 4.0   # короче — обычно поддакивания, а повторная расшифровка таких кусков даёт мусор
GAP_ROUNDS = 3
SAMPLE_RATE = 16000
# Фразы, которые Whisper выдумывает на тишине и шуме (выучены на субтитрах и стенограммах)
HALLUCINATION_RE = re.compile(r"субтитр|редактор|корректор|сенат отыр|стенограф|продолжение следует|подписывайтесь",
                              re.IGNORECASE)


def low_vram():
    return os.getenv("LOW_VRAM", "true").strip().lower() in ("1", "true", "yes")


def get_configured_models():
    """Модель LLM. Имя как в воркерах: golden_set.py подменяет его через --model."""
    return [os.getenv("OLLAMA_MODEL", "qwen3:4b")]


def llm_model_for(language):
    """LLM для звонка: на казахском можно использовать отдельную модель."""
    return (language == "kk" and os.getenv("OLLAMA_MODEL_KK", "").strip()) or get_configured_models()[0]


# ---------------------------------------------------------------------------
# Шаг 1. Распознавание речи
# ---------------------------------------------------------------------------

_asr_model = None
_asr_key = None


def asr_settings():
    return {
        "model": os.getenv("ASR_MODEL", "large-v3-turbo"),
        "device": os.getenv("ASR_DEVICE", "cuda"),
        "compute_type": os.getenv("ASR_COMPUTE_TYPE", "int8"),
        "language": os.getenv("ASR_LANGUAGE", "").strip() or None,
        "beam_size": int(os.getenv("ASR_BEAM_SIZE", "5")),
    }


def get_asr_model(settings):
    global _asr_model, _asr_key
    key = (settings["model"], settings["device"], settings["compute_type"])
    if _asr_model is None or _asr_key != key:
        from faster_whisper import WhisperModel
        release_asr()
        logging.info(f"Загрузка ASR {key}...")
        _asr_model = WhisperModel(settings["model"], device=settings["device"],
                                  compute_type=settings["compute_type"])
        _asr_key = key
    return _asr_model


def release_asr():
    """Освобождает видеопамять, чтобы на маленькой карте поместилась LLM."""
    global _asr_model, _asr_key
    _asr_model = None
    _asr_key = None
    gc.collect()


def kazakh_word_share(segments):
    """Доля слов с буквами казахского алфавита. Whisper часто называет казахский звонок русским, поэтому смотрим на текст."""
    words = re.findall(r"[^\W\d_]+", " ".join(s["text"] for s in segments).lower())
    if not words:
        return 0.0
    return sum(1 for word in words if KAZAKH_LETTERS & set(word)) / len(words)


def kazakh_settings(settings):
    """Настройки распознавания казахской моделью или None, если модели нет."""
    model = os.getenv("ASR_MODEL_KK", os.path.join("test", "models", "kazakh-turbo-int8"))
    path = model if os.path.isabs(model) else os.path.join(BASE_DIR, model)
    if not os.path.isdir(path):
        logging.warning(f"Казахская модель не найдена: {path}. Остаётся расшифровка обычной моделью.")
        return None
    return {**settings, "model": path, "language": "kk", "word_timestamps": True}


def phrases_from_words(words):
    """Разбивает слова на реплики по паузам."""
    phrases = []
    current = []
    for word in words:
        if current and word.start - current[-1].end >= PHRASE_PAUSE_SEC:
            phrases.append(current)
            current = []
        current.append(word)
    if current:
        phrases.append(current)
    return [{"start": round(p[0].start, 2), "end": round(p[-1].end, 2),
             "text": "".join(w.word for w in p).strip()} for p in phrases]


def collect_segments(segments_iter, offset=0.0):
    segments = []
    for s in segments_iter:
        if HALLUCINATION_RE.search(s.text):
            continue
        if s.words:
            phrases = phrases_from_words(s.words)
        elif s.text.strip():
            phrases = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}]
        else:
            continue
        for p in phrases:
            p["start"], p["end"] = round(p["start"] + offset, 2), round(p["end"] + offset, 2)
        segments.extend(phrases)
    return segments


def speech_gaps(speech, segments):
    """Куски речи (по детектору), где в расшифровке нет ни одной реплики: [(начало, конец)] в секундах."""
    covered = sorted((s["start"], s["end"]) for s in segments)
    gaps = []
    for start, end in speech:
        cursor = start
        for c_start, c_end in covered:
            if c_end <= cursor or c_start >= end:
                continue
            if c_start > cursor:
                gaps.append([cursor, c_start])
            cursor = max(cursor, c_end)
        if cursor < end:
            gaps.append([cursor, end])
    merged = []   # соседние куски речи с короткой паузой между ними расшифровываются вместе
    for gap in gaps:
        if merged and gap[0] - merged[-1][1] < 1.0:
            merged[-1][1] = gap[1]
        else:
            merged.append(gap)
    return [(float(a), float(b)) for a, b in merged if b - a >= GAP_MIN_SEC]


def transcribe(file_path, settings=None):
    """Возвращает {segments: [{start, end, text, language}], language, language_probability, duration, seconds}."""
    settings = settings or asr_settings()
    model = get_asr_model(settings)
    started = time.time()
    # multilingual: язык определяется по каждому фрагменту, это нужно для смешанных RU/KK звонков.
    # condition_on_previous_text=False: одна ошибка не тянется дальше и не зацикливается.
    from faster_whisper.audio import decode_audio
    options = dict(
        language=settings["language"],
        multilingual=settings["language"] is None,
        beam_size=settings["beam_size"],
        condition_on_previous_text=False,
        hotwords=ASR_HOTWORDS,
        word_timestamps=settings.get("word_timestamps", False),
    )
    audio = decode_audio(file_path, sampling_rate=SAMPLE_RATE)
    segments_iter, info = model.transcribe(audio, vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
                                           **options)
    segments = collect_segments(segments_iter)   # распознавание идёт здесь: генератор ленивый

    from faster_whisper.vad import VadOptions, get_speech_timestamps
    speech = [(t["start"] / SAMPLE_RATE, t["end"] / SAMPLE_RATE)
              for t in get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=500))]
    refilled = []
    # Whisper может перескочить и внутри заново расшифрованного куска, поэтому несколько кругов
    for _ in range(GAP_ROUNDS):
        gaps = [g for g in speech_gaps(speech, segments) if g not in refilled]
        if not gaps:
            break
        for gap_start, gap_end in gaps:
            clip = audio[int(gap_start * SAMPLE_RATE):int(gap_end * SAMPLE_RATE)]
            # без подсказок: на коротком куске Whisper начинает повторять их вместо речи
            clip_iter, _ = model.transcribe(clip, vad_filter=False, **{**options, "hotwords": None})
            confident = (s for s in clip_iter
                         if s.avg_logprob >= -1.0 and s.no_speech_prob < 0.6 and s.compression_ratio <= 2.4
                         and s.start < gap_end - gap_start)
            for p in collect_segments(confident, offset=gap_start):
                p["end"] = min(p["end"], round(gap_end, 2))
                segments.append(p)
        refilled.extend(gaps)
        segments.sort(key=lambda s: s["start"])
    if refilled:
        logging.info(f"Whisper пропустил речь, куски расшифрованы заново: "
                     + ", ".join(f"{a:.0f}–{b:.0f} с" for a, b in refilled))
    return {
        "segments": segments,
        "gaps_refilled": [[round(a, 1), round(b, 1)] for a, b in refilled],
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "kazakh_prob": round(dict(info.all_language_probs or []).get("kk", 0.0), 4),
        "duration": round(info.duration, 2),
        "seconds": round(time.time() - started, 2),
    }


def transcribe_routed(file_path, settings=None):
    """
    Распознаёт звонок обычной моделью, а если текст казахский, повторяет казахской.
    Возвращает результат transcribe с полями model и kazakh_share.
    """
    settings = settings or asr_settings()
    asr = transcribe(file_path, settings)
    share = round(kazakh_word_share(asr["segments"]), 3)
    asr.update(model=settings["model"], kazakh_share=share)
    # Текст обычной модели на казахском нестабилен от запуска к запуску, поэтому признаков два.
    # Whisper при этом называет такие звонки русскими, но вероятность казахского у них заметно выше нуля.
    is_kazakh = (share >= float(os.getenv("KAZAKH_WORD_SHARE", "0.15"))
                 or asr["kazakh_prob"] >= float(os.getenv("KAZAKH_LANG_PROB", "0.02")))
    if settings["language"] is not None or not is_kazakh:
        return asr

    kk_settings = kazakh_settings(settings)
    if kk_settings is None:
        return asr
    logging.info(f"Казахский звонок: казахских слов {share:.0%}, вероятность языка {asr['kazakh_prob']:.1%}. "
                 "Повторное распознавание казахской моделью...")
    first_pass_seconds = asr["seconds"]
    release_asr()
    result = transcribe(file_path, kk_settings)
    if not result["segments"]:
        logging.warning("Казахская модель не вернула текст, остаётся расшифровка обычной моделью.")
        return asr
    result.update(model=kk_settings["model"], kazakh_share=share, kazakh_prob=asr["kazakh_prob"],
                  seconds=round(result["seconds"] + first_pass_seconds, 2))
    return result


# ---------------------------------------------------------------------------
# Шаг 2. Анализ расшифровки
# ---------------------------------------------------------------------------

_LINES_SCHEMA_TEXT = '''  "lines": [
    {
      "i": <номер реплики из расшифровки>,
      "speaker": "Менеджер" или "Клиент",
      "sentiment": "positive" | "neutral" | "negative",
      "emotion": "Нейтральный" | "Радость" | "Грусть" | "Гнев" | "Раздражение"
    }
  ],
'''

_LINES_RULE_TEXT = '''1. РЕПЛИКИ (lines):
   - Для КАЖДОЙ реплики расшифровки верни один объект с её номером i. Реплики не пропускай и не переписывай их текст.
   - Определи говорящего по смыслу: менеджер отвечает от имени компании, называет цены, наличие и условия; клиент спрашивает и называет свою машину.
   - В расшифровке есть ошибки распознавания речи. Цифры, бренды и имена восстанавливай только если смысл однозначен.
'''


def build_text_prompt():
    """
    Текстовая версия общего промпта: инструкции и справочники остаются одни на всех
    провайдеров, убирается только то, что относится к прослушиванию аудио.
    Если структура промпта изменится, лучше упасть здесь, чем молча отправить модели мусор.
    """
    prompt = load_prompt("unified_analysis_prompt")
    prompt, n_intro = re.subn(
        r"Внимательно прослушай аудиозапись телефонного разговора и выполни комплексный глубокий анализ\.",
        "Тебе дана автоматическая расшифровка телефонного разговора, разбитая на пронумерованные реплики. "
        "Выполни комплексный анализ только по этому тексту.",
        prompt,
    )
    prompt, n_schema = re.subn(
        r'  "dialogue": \[.*?\n  \],\n  "acoustics": \{.*?\n  \},\n',
        lambda _: _LINES_SCHEMA_TEXT, prompt, flags=re.S,
    )
    prompt, n_rule = re.subn(
        r"^1\. ПОЛНАЯ ДОСЛОВНАЯ ТРАНСКРИПЦИЯ.*?(?=^2\. )",
        lambda _: _LINES_RULE_TEXT, prompt, flags=re.S | re.M,
    )
    if (n_intro, n_schema, n_rule) != (1, 1, 1):
        raise RuntimeError(f"Структура unified_analysis_prompt изменилась: {(n_intro, n_schema, n_rule)}")
    return prompt


PROMPT_UNIFIED_ANALYSIS = build_text_prompt()


def _object(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or list(properties)}


# Гарантирует структуру ответа. Содержание остальных полей задаёт промпт.
ANALYSIS_SCHEMA = _object({
    "lines": {"type": "array", "items": _object({
        "i": {"type": "integer"},
        "speaker": {"enum": ["Менеджер", "Клиент"]},
        "sentiment": {"enum": ["positive", "neutral", "negative"]},
        "emotion": {"enum": ["Нейтральный", "Радость", "Грусть", "Гнев", "Раздражение"]},
    })},
    "overall_sentiment": _object({
        "satisfaction_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "manager_politeness": {"type": "integer", "minimum": 1, "maximum": 5},
        "customer_sentiment_start": {"type": "string"},
        "customer_sentiment_end": {"type": "string"},
        "conflict_detected": {"type": "boolean"},
        "conflict_reason": {"type": ["string", "null"]},
        "tone_summary": {"type": "string"},
    }),
    "commercial": {
        "type": "object",
        "properties": {"sale_status": {"enum": SALE_STATUSES},
                       "call_summary": {"type": "string"},
                       "call_category": {"type": "string"},
                       "call_subcategory": {"type": "string"}},
        "required": ["sale_status", "call_summary", "call_category", "call_subcategory"],
    },
    "checklist": {"type": "array", "minItems": CHECKLIST_SIZE, "maxItems": CHECKLIST_SIZE, "items": _object({
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "title": {"type": "string"},
        "status": {"enum": ["passed", "failed", "not_applicable"]},
        "quote": {"type": "string"},
        "comment": {"type": "string"},
    })},
})


class Heartbeat:
    """Раз в минуту пишет в лог, что долгий шаг ещё идёт: иначе анализ выглядит как зависание."""

    def __init__(self, message, interval=60):
        self.message = message
        self.interval = interval

    def _run(self):
        started = time.time()
        while not self._stop.wait(self.interval):
            minutes, seconds = divmod(int(time.time() - started), 60)
            logging.info(f"{self.message}, прошло {minutes:02d}:{seconds:02d}")

    def __enter__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


def format_transcript(segments):
    return "\n".join(f"{i}. [{s['start']:.1f}] {s['text']}" for i, s in enumerate(segments))


def ollama_chat(transcript_text, model):
    """
    Один запрос к Ollama. Возвращает (текст ответа, статистика).
    Ответ читается потоком, чтобы следить за скоростью: если она ниже MIN_TOKENS_PER_SEC,
    запрос обрывается (Ollama при этом останавливает генерацию) и поднимается SlowGenerationError.
    """
    request = {
        "model": model,
        "stream": True,
        "think": False,
        "format": ANALYSIS_SCHEMA,
        "keep_alive": 0 if low_vram() else "30m",
        "options": {"temperature": 0.2, "num_ctx": OLLAMA_NUM_CTX, "num_predict": OLLAMA_NUM_PREDICT},
        "messages": [
            {"role": "system", "content": PROMPT_UNIFIED_ANALYSIS},
            {"role": "user", "content": f"РАСШИФРОВКА ЗВОНКА:\n{transcript_text}"},
        ],
    }
    parts, data, tokens, first_token_at = [], {}, 0, None
    with requests.post(f"{OLLAMA_URL}/api/chat", json=request, stream=True, timeout=OLLAMA_TIMEOUT) as response:
        if response.status_code != 200:
            raise RuntimeError(f"Ollama HTTP {response.status_code}: {response.text[:300]}")
        for line in response.iter_lines(chunk_size=1):   # без буфера: иначе токены приходят пачками и скорость считается с опозданием
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get("error"):
                raise RuntimeError(f"Ollama: {str(chunk['error'])[:300]}")
            piece = (chunk.get("message") or {}).get("content", "")
            if piece:
                parts.append(piece)
                tokens += 1
                now = time.time()
                first_token_at = first_token_at or now
                elapsed = now - first_token_at
                if MIN_TOKENS_PER_SEC and elapsed >= SPEED_CHECK_SEC and tokens / elapsed < MIN_TOKENS_PER_SEC:
                    raise SlowGenerationError(
                        f"скорость {tokens / elapsed:.2f} ток/с ниже порога {MIN_TOKENS_PER_SEC} "
                        f"(за {int(elapsed)} с написано {tokens} токенов), анализ прерван")
            if chunk.get("done"):
                data = chunk
    eval_ns = data.get("eval_duration") or 0
    return "".join(parts), {
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "prompt_sec": round((data.get("prompt_eval_duration") or 0) / 1e9, 1),
        "output_sec": round(eval_ns / 1e9, 1),
        "total_sec": round((data.get("total_duration") or 0) / 1e9, 1),
        "tokens_per_sec": round(data["eval_count"] / (eval_ns / 1e9), 1) if eval_ns and data.get("eval_count") else None,
        "done_reason": data.get("done_reason"),
    }


def analyze_transcript(segments, model=None):
    """Возвращает (ответ LLM в виде словаря, статистика последнего запроса)."""
    model = model or get_configured_models()[0]
    transcript_text = format_transcript(segments)
    last_error = None
    for attempt in range(1, LLM_ATTEMPTS + 1):
        content, stats = ollama_chat(transcript_text, model)
        stats["attempts"] = attempt
        try:
            return json.loads(content), stats
        except ValueError as e:
            # Чаще всего done_reason=length: ответ упёрся в OLLAMA_NUM_PREDICT
            last_error = f"невалидный JSON ({stats['done_reason']}): {e}"
            logging.warning(f"Ollama {model}, попытка {attempt}/{LLM_ATTEMPTS}: {last_error}")
    raise RuntimeError(f"Ollama {model}: {last_error}")


def acoustics_from_dialogue(dialogue, duration):
    """Считает то, что Gemini оценивал на слух. Темп — слова в минуту речи."""
    by_speaker = {"Менеджер": 0.0, "Клиент": 0.0}
    words = 0
    for d in dialogue:
        length = max(0.0, d["end_sec"] - d["start_sec"])
        by_speaker[d["speaker"]] = by_speaker.get(d["speaker"], 0.0) + length
        words += len(d["text"].split())
    speech = sum(by_speaker.values())
    return {
        "speech_tempo_wpm": round(words / (speech / 60)) if speech else 0,
        "silence_sec": round(max(0.0, duration - speech), 1),
        "manager_speech_sec": round(by_speaker["Менеджер"], 1),
        "client_speech_sec": round(by_speaker["Клиент"], 1),
    }


def merge_result(asr, llm):
    """Собирает ответ в формате Gemini: текст и таймкоды от ASR, разметка реплик от LLM."""
    labels = {}
    for line in llm.get("lines") or []:
        if isinstance(line, dict) and isinstance(line.get("i"), int):
            labels[line["i"]] = line

    dialogue = []
    for i, seg in enumerate(asr["segments"]):
        label = labels.get(i, {})
        dialogue.append({
            "speaker": label.get("speaker") or "Клиент",
            "start_sec": seg["start"],
            "end_sec": seg["end"],
            "text": seg["text"],
            "sentiment": label.get("sentiment") or "neutral",
            "emotion": label.get("emotion") or "Нейтральный",
        })

    # passed и score однозначно следуют из status: считаем сами, а не доверяем модели
    checklist = []
    for item in llm.get("checklist") or []:
        status = item.get("status")
        checklist.append({
            **item,
            "passed": {"passed": True, "failed": False}.get(status),
            "score": {"passed": 100, "failed": 0}.get(status),
        })

    return {
        "dialogue": dialogue,
        "acoustics": acoustics_from_dialogue(dialogue, asr["duration"]),
        "overall_sentiment": llm.get("overall_sentiment") or {},
        "commercial": llm.get("commercial") or {},
        "checklist": checklist,
    }


def validate_result(result, segments_count):
    """Список проблем со структурой ответа. Пустой — ответ можно сохранять."""
    problems = []
    commercial = result.get("commercial") or {}
    if commercial.get("sale_status") not in SALE_STATUSES:
        problems.append(f"sale_status={commercial.get('sale_status')!r}")
    if not commercial.get("call_summary"):
        problems.append("нет call_summary")
    if len(result.get("checklist") or []) != CHECKLIST_SIZE:
        problems.append(f"в чек-листе {len(result.get('checklist') or [])} пунктов")
    return problems


def dialogue_text(dialogue):
    return "\n\n".join(f"**{d['speaker']}:** {d['text']}" for d in dialogue)


def transcribe_and_analyze_local(file_path, output_txt_path=None, meta_out=None):
    """Полный локальный анализ. Подпись как у transcribe_and_analyze_with_gemini."""
    file_path = os.path.abspath(file_path)
    asr_cfg = asr_settings()
    logging.info(f"[ЭТАП 1/4] Whisper: запуск, модель {os.path.basename(asr_cfg['model'])}")
    asr = transcribe_routed(file_path, asr_cfg)
    if low_vram():
        release_asr()
    if not asr["segments"]:
        raise RuntimeError("Распознавание не вернуло ни одной реплики")
    words = sum(len(s["text"].split()) for s in asr["segments"])
    logging.info(f"[ЭТАП 1/4] Whisper: текст получен за {asr['seconds']} с, модель {os.path.basename(asr['model'])}, "
                 f"язык {asr['language']}, аудио {asr['duration']} с, реплик {len(asr['segments'])}, слов {words}")

    model = llm_model_for(asr["language"])
    logging.info(f"[ЭТАП 2/4] {model}: запуск анализа текста, ждём JSON")
    with Heartbeat(f"[ЭТАП 2/4] {model}: анализ идёт"):
        llm, stats = analyze_transcript(asr["segments"], model)
    result = merge_result(asr, llm)
    problems = validate_result(result, len(asr["segments"]))
    if problems:
        raise RuntimeError(f"Ответ {model} не прошёл проверку: {'; '.join(problems)}")
    logging.info(f"[ЭТАП 2/4] {model}: JSON получен за {stats['total_sec']} с, {stats['tokens_per_sec']} ток/с, "
                 f"попытка {stats['attempts']}, проверка структуры пройдена")

    full_text = dialogue_text(result["dialogue"])
    if output_txt_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_txt_path)), exist_ok=True)
        with open(output_txt_path, "w", encoding="utf-8") as f:
            f.write(full_text)

    if meta_out is not None:
        from analysis_runs import prompt_version
        meta_out.update({
            "provider": "ollama",
            "model": f"{os.path.basename(asr['model'])}+{model}",
            "prompt_version": prompt_version(PROMPT_UNIFIED_ANALYSIS),
        })
    return result, full_text
