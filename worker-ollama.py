# -*- coding: utf-8 -*-
"""
worker-ollama.py — воркер без облака: faster-whisper + Ollama (см. local_pipeline.py).

Очередь, повторы, перемещение файлов и сохранение в базу берутся из worker.py,
заменяется только шаг анализа. Поэтому в логе (worker.log) встречаются сообщения
со словом «Gemini»: фактически анализ идёт локально, в базе provider = ollama.

Ход обработки виден в логе по строкам [ЭТАП 1/4] … [ЭТАП 4/4]:
  1 — Whisper: текст, 2 — LLM: JSON, 3 — запись в базу, 4 — итог звонка.

Запуск: Ollama должна работать, затем
  python worker-ollama.py
Перед запуском остановите worker.py и worker-openrouter.py: они слушают ту же папку incoming/.
"""

import logging
import sys
import time

import requests

import worker
from local_pipeline import (OLLAMA_URL, asr_settings, get_configured_models, low_vram,
                            transcribe_and_analyze_local)

_call_started = 0.0


def check_ollama():
    """Без Ollama все файлы ушли бы в повторы, поэтому проверяем сразу при запуске."""
    model = get_configured_models()[0]
    try:
        tags = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json().get("models", [])
    except requests.RequestException as e:
        sys.exit(f"Ollama недоступна по адресу {OLLAMA_URL}: {e}")
    names = [m.get("name") for m in tags]
    if model not in names:
        sys.exit(f"Модели {model} нет в Ollama. Скачайте: ollama pull {model}. Есть: {names}")


def analyze(file_path, output_txt_path=None, meta_out=None):
    global _call_started
    _call_started = time.time()
    return transcribe_and_analyze_local(file_path, output_txt_path, meta_out=meta_out)


def wrap_save(save_unified_analysis):
    """Добавляет к сохранению в базу строки этапов 3 и 4."""

    def save(call_id, file_path, analysis_data, full_text, manager_name=None):
        logging.info(f"[ЭТАП 3/4] База: запись результата, call_id={call_id}")
        result = save_unified_analysis(call_id, file_path, analysis_data, full_text, manager_name)
        logging.info(f"[ЭТАП 3/4] База: результат записан, call_id={call_id}")
        commercial = analysis_data.get("commercial") or {}
        tone = analysis_data.get("overall_sentiment") or {}
        logging.info(f"[ЭТАП 4/4] Итог: {commercial.get('sale_status')}, {commercial.get('call_category')}, "
                     f"CSAT {tone.get('satisfaction_score')}, менеджер {manager_name or 'не определён'}, "
                     f"всего {int(time.time() - _call_started)} с")
        return result

    return save


def main():
    check_ollama()
    logging.getLogger("httpx").setLevel(logging.WARNING)   # запросы к huggingface засоряют лог этапов
    logging.info(f"Локальный воркер: ASR {asr_settings()}, LLM {get_configured_models()}, LOW_VRAM={low_vram()}")
    worker.transcribe_and_analyze_with_gemini = analyze

    import ton_voice_processor
    ton_voice_processor.save_unified_analysis = wrap_save(ton_voice_processor.save_unified_analysis)
    worker.main()


if __name__ == "__main__":
    main()
