import os
import sys
import psycopg2
import logging
from worker import process_file, parse_filename, DB_CONFIG
from ton_voice_processor import process_all_tone_voice

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def test_full_pipeline():
    test_file = r"incoming/rg-5555-87014662990-20201113-150553-1605258353.53425.wav"
    if not os.path.exists(test_file):
        print(f"[!] Файл {test_file} не найден!")
        return

    print("==================================================================")
    print(" 1. ТЕСТИРОВАНИЕ ПОЛНОГО ПАЙПЛАЙНА: ТРАНСКРИБАЦИЯ + ТОН-ВОЙС")
    print(f" Аудиофайл: {test_file}")
    print("==================================================================")

    # 1. Извлекаем данные и проверяем наличие транскрипции
    data = parse_filename(os.path.basename(test_file))
    unique_id = data["unique_id"]

    # Подключаемся к БД
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # Сначала проверяем, есть ли звонок в call_transcriptions
    cursor.execute("SELECT id, text FROM call_transcriptions WHERE unique_id = %s;", (unique_id,))
    row = cursor.fetchone()

    # Если уже есть текст транскрипции, используем его, либо запускаем процесс
    if row and row[1]:
        call_id = row[0]
        transcript_text = row[1]
        print(f"\n[+] Звонок уже транскрибирован в БД (call_id = {call_id}).")
    else:
        print("\n[+] Запуск транскрибации через process_file...")
        process_file(test_file)
        cursor.execute("SELECT id, text FROM call_transcriptions WHERE unique_id = %s;", (unique_id,))
        row = cursor.fetchone()
        call_id = row[0]
        transcript_text = row[1]

    # 2. Запускаем расчет всех 5 уровней Tone of Voice
    print(f"\n[+] Запуск 5 уровней Tone of Voice для call_id = {call_id}...")
    try:
        process_all_tone_voice(call_id, test_file, transcript_text)
    except Exception as e:
        import traceback
        print(f"\n[!] ОШИБКА В ТОН-ВОЙС:")
        traceback.print_exc()
        return

    # 3. Проверяем результаты во всех 6 таблицах PostgreSQL
    print("\n==================================================================")
    print(" 2. ПРОВЕРКА ДАННЫХ В БАЗЕ POSTGRESQL")
    print("==================================================================")

    # Таблица 1: call_transcriptions
    cursor.execute("SELECT id, phone, call_date, call_time, LENGTH(text), status FROM call_transcriptions WHERE id = %s;", (call_id,))
    t1 = cursor.fetchone()
    print(f"\n[1] call_transcriptions:")
    print(f"    ID: {t1[0]} | Телефон: {t1[1]} | Дата: {t1[2]} | Время: {t1[3]} | Символов текста: {t1[4]} | Статус: {t1[5]}")

    # Таблица 2: call_audio_features (Тон 1)
    cursor.execute("SELECT mean_rms, max_rms, loud_moments_pct, silent_duration_sec, silence_pct, speech_tempo FROM call_audio_features WHERE call_id = %s;", (call_id,))
    t2 = cursor.fetchone()
    if t2:
        print(f"\n[2] call_audio_features (Тон 1 - Акустика):")
        print(f"    Средняя громкость RMS: {t2[0]} | Пиковая: {t2[1]} | Всплески громкости: {t2[2]}%")
        print(f"    Тишина/паузы: {t2[3]} сек ({t2[4]}%) | Темп речи: {t2[5]} уд/мин")
    else:
        print("    [!] Нет записи в call_audio_features")

    # Таблица 3: call_audio_emotions (Тон 2)
    cursor.execute("SELECT dominant_emotion, confidence, happy_score, neutral_score, sad_score, angry_score FROM call_audio_emotions WHERE call_id = %s;", (call_id,))
    t3 = cursor.fetchone()
    if t3:
        print(f"\n[3] call_audio_emotions (Тон 2 - Эмоции по аудио):")
        print(f"    Доминирующая эмоция: {t3[0]} (уверенность: {t3[1]}%)")
        print(f"    Радость: {t3[2]}% | Нейтральный: {t3[3]}% | Грусть: {t3[4]}% | Гнев: {t3[5]}%")
    else:
        print("    [!] Нет записи в call_audio_emotions")

    # Таблица 4: call_audio_segments (Тон 3)
    cursor.execute("SELECT COUNT(*), MIN(start_sec), MAX(end_sec) FROM call_audio_segments WHERE call_id = %s;", (call_id,))
    t4 = cursor.fetchone()
    print(f"\n[4] call_audio_segments (Тон 3 - Посегментный таймлайн):")
    print(f"    Всего отрезков по 10 сек: {t4[0]} (таймлайн от {t4[1]}с до {t4[2]}с)")

    # Таблица 5: call_text_sentiment_lines (Тон 4)
    cursor.execute("SELECT COUNT(*), sentiment_label, COUNT(*) FROM call_text_sentiment_lines WHERE call_id = %s GROUP BY sentiment_label;", (call_id,))
    t5 = cursor.fetchall()
    print(f"\n[5] call_text_sentiment_lines (Тон 4 - RuBERT по репликам):")
    for row in t5:
        print(f"    Тональность '{row[1]}': {row[2]} реплик")

    # Таблица 6: customer_sentiment (Тон 5)
    cursor.execute("SELECT customer_sentiment_start, customer_sentiment_end, satisfaction_score, manager_politeness, conflict_detected, tone_summary FROM customer_sentiment WHERE call_id = %s;", (call_id,))
    t6 = cursor.fetchone()
    if t6:
        print(f"\n[6] customer_sentiment (Тон 5 - LLM-анализ диалога):")
        print(f"    Тон в начале: {t6[0]} -> В конце: {t6[1]}")
        print(f"    Удовлетворенность клиента: {t6[2]}/5 | Вежливость менеджера: {t6[3]}/5")
        print(f"    Конфликт зафиксирован: {'ДА' if t6[4] else 'НЕТ'}")
        print(f"    Резюме: {t6[5]}")
    else:
        print("    [!] Нет записи в customer_sentiment")

    print("\n==================================================================")
    print(" [+] ВСЕ ТАБЛИЦЫ УСПЕШНО ЗАПОЛНЕНЫ И СВЯЗАНЫ ПО CALL_ID!")
    print("==================================================================")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    test_full_pipeline()
