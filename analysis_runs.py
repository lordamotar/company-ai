# -*- coding: utf-8 -*-
"""
analysis_runs.py — версионирование результатов AI-анализа звонков.

Каждый прогон анализа (боевой или проверочный) сохраняется в call_analysis_runs
с указанием провайдера, модели и версии промпта. Это позволяет:
  * понимать, какой моделью и каким промптом получен результат в карточке звонка;
  * сравнивать Gemini / OpenRouter / локальные модели на эталонном наборе (golden_set.py).

Требует миграции: python apply_migrations.py (раздел «Версионирование AI-анализа» в init_tone_tables.sql).
"""

import os
import json
import shutil
import hashlib
import logging
import tempfile

import psycopg2

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "company"),
    "user": os.getenv("DB_USER", "company_user"),
    "password": os.getenv("DB_PASSWORD", "company_password"),
}


def prompt_version(prompt_text):
    """Версия промпта = первые 10 символов sha256 его текста. Меняется при любой правке промпта."""
    return hashlib.sha256((prompt_text or "").strip().encode("utf-8")).hexdigest()[:10]


def make_anonymized_copy(file_path):
    """
    Создаёт временную копию аудиофайла с нейтральным именем (без номера телефона и даты),
    чтобы в облачный сервис не уходили метаданные из имени файла АТС.
    Вызывающий код обязан удалить копию (remove_quietly).
    """
    ext = os.path.splitext(file_path)[1] or ".wav"
    fd, tmp_path = tempfile.mkstemp(prefix="call_", suffix=ext)
    os.close(fd)
    shutil.copy2(file_path, tmp_path)
    return tmp_path


def remove_quietly(path):
    """Удаляет временный файл; ошибка удаления не критична, но пишется в лог."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logging.warning(f"Не удалось удалить временный файл {path}: {e}")


def record_run(unique_id, provider, model, prompt_ver, result,
               call_id=None, duration_ms=None, purpose="production", update_call=True):
    """
    Сохраняет прогон анализа в call_analysis_runs.
    При update_call=True также отмечает в call_transcriptions, какой моделью и промптом сделан текущий анализ.

    Сбой записи НЕ прерывает обработку звонка (анализ уже сохранён и повторно платить за него незачем),
    но пишется в лог как ошибка — чаще всего это значит, что не применена миграция.
    Возвращает id записи прогона или None.
    """
    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO call_analysis_runs (unique_id, call_id, provider, model, prompt_version, purpose, duration_ms, result)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            str(unique_id), call_id, provider, model, prompt_ver, purpose, duration_ms,
            json.dumps(result, ensure_ascii=False) if result is not None else None,
        ))
        run_id = cur.fetchone()[0]

        if update_call and call_id:
            cur.execute("""
                UPDATE call_transcriptions
                SET analysis_provider = %s, analysis_model = %s, prompt_version = %s, analyzed_at = now()
                WHERE id = %s;
            """, (provider, model, prompt_ver, call_id))

        conn.commit()
        cur.close()
        logging.info(f"Прогон анализа записан: run_id={run_id}, {provider}/{model}, промпт {prompt_ver}, цель={purpose}.")
        return run_id
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(
            f"[!] Не удалось записать версию анализа для {unique_id}: {e}. "
            f"Сам анализ сохранён. Проверьте, что применена миграция (python apply_migrations.py)."
        )
        return None
    finally:
        if conn:
            conn.close()
