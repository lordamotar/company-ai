"""
Скрипт повторного качественного анализа звонков через Gemini API (без Groq)
Перепрогоняет звонки с неполными данными (пустой CSAT / вежливость / чек-лист),
исправляет таймлайны и сохраняет ручные правки РОПа.
"""
import os
import sys
import time
import logging
import psycopg2
from app.services.audio_service import find_audio_file
from worker import transcribe_and_analyze_with_gemini, detect_manager_from_text, get_operator_by_extension
from ton_voice_processor import save_unified_analysis
from export_db_to_json import DB_CONFIG, export_calls_to_json, export_audit_logs_to_json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

def find_incomplete_calls():
    """Находит звонки 2026 года без CSAT или без детального анализа."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.unique_id, t.source_file, t.phone, t.manager_name, t.city_name, cs.satisfaction_score
        FROM call_transcriptions t
        LEFT JOIN customer_sentiment cs ON t.id = cs.call_id
        WHERE (t.call_date >= '2026-01-01' OR t.call_date IS NULL)
          AND (
               cs.satisfaction_score IS NULL 
            OR cs.manager_politeness IS NULL
            OR t.checklist IS NULL
            OR jsonb_array_length(CASE WHEN jsonb_typeof(t.checklist::jsonb) = 'array' THEN t.checklist::jsonb ELSE '[]'::jsonb END) = 0
          )
        ORDER BY t.id ASC;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# Интервал между запросами к Gemini API для защиты от лимитов (RPM / RPD)
CALL_DELAY_SEC = int(os.getenv("CALL_DELAY_SEC", "6"))

def reprocess_call(call_row):
    call_id, unique_id, source_file, phone, current_manager, current_city, cur_csat = call_row
    logging.info(f"\n--- Перепрогон звонка #{call_id} (файл: {source_file}, менеджер: {current_manager}) ---")

    # 1. Проверка: если звонок уже успешно прогнан ранее, повторно не трогаем!
    try:
        conn_chk = psycopg2.connect(**DB_CONFIG)
        cur_chk = conn_chk.cursor()
        cur_chk.execute("""
            SELECT cs.satisfaction_score, cs.manager_politeness, t.checklist
            FROM call_transcriptions t
            LEFT JOIN customer_sentiment cs ON t.id = cs.call_id
            WHERE t.id = %s;
        """, (call_id,))
        chk_row = cur_chk.fetchone()
        cur_chk.close()
        conn_chk.close()

        if chk_row and chk_row[0] is not None and chk_row[1] is not None and chk_row[2] is not None:
            chk_list = chk_row[2]
            if isinstance(chk_list, list) and len(chk_list) > 0:
                logging.info(f"[↷] Звонок #{call_id} уже полностью обработан (CSAT={chk_row[0]}/5, вежливость={chk_row[1]}/5, чек-лист={len(chk_list)} пунктов). Пропускаем.")
                return "ALREADY_DONE"
    except Exception as e_chk:
        logging.warning(f"Ошибка проверки статуса звонка #{call_id}: {e_chk}")

    # 2. Ищем аудиофайл
    audio_path = find_audio_file(call_id=call_id, source_file=source_file, unique_id=unique_id, phone=phone)
    if not audio_path or not os.path.isfile(audio_path):
        logging.warning(f"[-] Аудиофайл для звонка #{call_id} не найден на диске. Пропуск.")
        return False

    logging.info(f"[+] Найден аудиофайл: {audio_path}")
    
    # 3. Запускаем единый мультимодальный анализ Gemini
    try:
        analysis_data, text = transcribe_and_analyze_with_gemini(audio_path)
        logging.info(f"[+] Анализ Gemini успешно получен для #{call_id}")

        # Определяем менеджера:
        # 1. Если РОП уже поставил менеджера (не 'Оператор'), сохраняем его!
        # 2. Иначе ищем в тексте приветствия
        # 3. Иначе по внутреннему номеру
        manager_to_save = None
        if current_manager and current_manager != 'Оператор':
            manager_to_save = current_manager
        else:
            detected_from_text = detect_manager_from_text(text)
            if detected_from_text:
                manager_to_save = detected_from_text
            elif source_file:
                import re
                m = re.search(r"(?:rg|out|in|incoming)-(\d+)-", os.path.basename(source_file), re.I)
                if m:
                    manager_to_save = get_operator_by_extension(m.group(1))

        # Сохраняем в базу данных
        save_unified_analysis(
            call_id=call_id,
            file_path=audio_path,
            analysis_data=analysis_data,
            full_text=text,
            manager_name=manager_to_save
        )
        logging.info(f"[✓] Звонок #{call_id} успешно обновлен в БД.")
        return True

    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
            logging.error(f"[!] Лимит квоты Gemini исчерпан на звонке #{call_id}: {err_str}")
            return "QUOTA_EXHAUSTED"
        logging.error(f"[-] Ошибка при анализе звонка #{call_id}: {e}")
        return False

def main():
    logging.info(f"=== Запуск reprocess_calls.py (Интервал между звонками: {CALL_DELAY_SEC} сек.) ===")
    
    # Позволяет передать ID звонков аргументами: python reprocess_calls.py 97 95 90
    target_ids = [int(arg) for arg in sys.argv[1:] if arg.isdigit()]
    
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    if target_ids:
        cur.execute("""
            SELECT t.id, t.unique_id, t.source_file, t.phone, t.manager_name, t.city_name, cs.satisfaction_score
            FROM call_transcriptions t
            LEFT JOIN customer_sentiment cs ON t.id = cs.call_id
            WHERE t.id = ANY(%s)
            ORDER BY t.id ASC;
        """, (target_ids,))
        calls_to_process = cur.fetchall()
    else:
        calls_to_process = find_incomplete_calls()
    cur.close()
    conn.close()

    total = len(calls_to_process)
    logging.info(f"Найдено звонков для перепрогона: {total}")

    success_count = 0
    skipped_count = 0

    for idx, call_row in enumerate(calls_to_process, start=1):
        logging.info(f"\n[Прогресс: {idx}/{total}]")
        status = reprocess_call(call_row)
        if status == "QUOTA_EXHAUSTED":
            logging.error("\n[!] Остановка: Лимит квоты Google Gemini API исчерпан (429).")
            logging.warning("Перепрогон прерван, чтобы не расходовать попытки впустую.")
            break
        elif status == "ALREADY_DONE":
            skipped_count += 1
        elif status is True:
            success_count += 1
            # Пауза между реальными запросами к Gemini API для соблюдения лимитов
            if idx < total:
                logging.info(f"Пауза {CALL_DELAY_SEC} сек. перед следующим запросом...")
                time.sleep(CALL_DELAY_SEC)

    logging.info(f"\n=== ИТОГ: успешно обработано: {success_count}, пропущено (уже готовые): {skipped_count}, всего в очереди: {total} ===")
    
    logging.info("Синхронизация JSON файлов для веб-интерфейса...")
    export_calls_to_json()
    export_audit_logs_to_json()
    logging.info("Дашборд обновлен свежими данными.")

if __name__ == "__main__":
    main()
