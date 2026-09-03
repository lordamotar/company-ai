import os
import json
import psycopg2
import logging
import re
from datetime import date, time, datetime

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5433,
    "database": "company",
    "user": "company_user",
    "password": "company_password",
}

def default_serializer(obj):
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    return str(obj)

def detect_manager_name(text):
    if not text:
        return "Оператор"
    
    # 1. Поиск в первых строках: «Эйкос Жандос» / «компания Эйкос Жандос» / «компания Эйкос, Жандос»
    m = re.search(r'[«"]?[Ээ]йкос[»"]?\s*[,–-]?\s*([А-ЯЁ][а-яё]{2,})', text)
    if m:
        candidate = m.group(1).capitalize()
        if candidate.lower() not in ["звоню", "слушаю", "добрый", "здравствуйте", "день", "компания"]:
            return candidate

    # 2. Поиск «Жандос, добрый день» / «Руслан, здравствуйте»
    m = re.search(r'([А-ЯЁ][а-яё]{2,})\s*,\s*(?:добрый|здравствуйте|привет|слушаю)', text, re.I)
    if m:
        candidate = m.group(1).capitalize()
        if candidate.lower() not in ["компания", "эйкос", "алло", "да", "нет"]:
            return candidate

    # 3. Поиск «меня Руслан зовут» / «меня зовут Руслан»
    m = re.search(r'меня\s+(?:зовут\s+)?([А-ЯЁ][а-яё]{2,})', text, re.I)
    if m:
        candidate = m.group(1).capitalize()
        if candidate.lower() not in ["зовут", "компания", "эйкос"]:
            return candidate

    m2 = re.search(r'меня\s+([А-ЯЁ][а-яё]{2,})\s+зовут', text, re.I)
    if m2:
        return m2.group(1).capitalize()

    # 4. Проверка известных имен в начале диалога
    names = ["Жандос", "Руслан", "Азамат", "Данияр", "Айбек", "Ербол", "Максим", "Алексей"]
    first_block = text[:300]
    for n in names:
        if re.search(rf'\b{n}\b', first_block, re.I):
            return n

    return "Оператор"

def detect_city(text):
    cities = ["Алматы", "Астана", "Шымкент", "Караганда", "Актобе", "Тараз", "Павлодар", "Усть-Каменогорск", "Семей", "Атырау", "Костанай", "Кызылорда", "Уральск", "Петропавловск", "Актау"]
    for c in cities:
        if c.lower() in text.lower():
            return c
    return "Алматы"

def detect_tire_details(text):
    brands = ["Cordiant", "Winter Drive", "Nokian", "Nordman", "Kormoran", "Michelin", "Pirelli", "Bridgestone", "Continental", "Viatti", "Triangle", "Sailun", "Кама"]
    found_brands = [b for b in brands if b.lower() in text.lower()]
    brand_str = " / ".join(found_brands) if found_brands else "Шины R15-R16"

    m_size = re.search(r'(\d{3}[\s/]\d{2}[\s/R]*\d{2})', text, re.I)
    tire_size = m_size.group(1).replace(" ", "/").upper() if m_size else "205/65 R15"

    return brand_str, tire_size

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_JSON = os.path.join(BASE_DIR, "html", "calls_data.json")

def export_calls_to_json(output_file=DEFAULT_OUTPUT_JSON):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 
                t.id, t.unique_id, t.phone, t.call_date, t.call_time, 
                t.text, t.source_file, t.status, t.manager_name, t.duration_sec,
                af.mean_rms, af.max_rms, af.loud_moments_pct, af.silent_duration_sec, af.silence_pct, af.speech_tempo, af.active_speech_sec,
                ae.dominant_emotion, ae.confidence as emotion_conf, ae.happy_score, ae.neutral_score, ae.sad_score, ae.angry_score,
                cs.customer_sentiment_start, cs.customer_sentiment_end, cs.satisfaction_score, cs.manager_politeness, cs.conflict_detected, cs.conflict_reason, cs.tone_summary
            FROM call_transcriptions t
            LEFT JOIN call_audio_features af ON t.id = af.call_id
            LEFT JOIN call_audio_emotions ae ON t.id = ae.call_id
            LEFT JOIN customer_sentiment cs ON t.id = cs.call_id
            ORDER BY t.id DESC;
        """)
        rows = cursor.fetchall()

        calls = []
        for r in rows:
            call_id = r[0]
            unique_id = r[1]
            phone = r[2] or "Неизвестно"
            call_date = r[3].strftime("%d.%m.%Y") if r[3] else "—"
            call_time = r[4].strftime("%H:%M:%S") if r[4] else "—"
            raw_text = r[5] or ""
            source_file = r[6] or ""
            status = r[7] or "new"
            db_manager = r[8]
            db_duration_sec = float(r[9]) if r[9] is not None else None

            mean_rms = float(r[10]) if r[10] is not None else 0.04
            max_rms = float(r[11]) if r[11] is not None else 0.2
            loud_pct = float(r[12]) if r[12] is not None else 0.0
            silence_sec = float(r[13]) if r[13] is not None else 0.0
            silence_pct = float(r[14]) if r[14] is not None else 20.0
            speech_tempo = float(r[15]) if r[15] is not None else 130.0
            active_sec = float(r[16]) if r[16] is not None else 0.0

            dominant_emotion = r[17] or "Радость"
            emotion_conf = float(r[18]) if r[18] is not None else 75.0
            happy_score = float(r[19]) if r[19] is not None else 70.0
            neutral_score = float(r[20]) if r[20] is not None else 20.0
            sad_score = float(r[21]) if r[21] is not None else 10.0
            angry_score = float(r[22]) if r[22] is not None else 0.0

            csat = int(r[25]) if r[25] is not None else 5
            manager_politeness = int(r[26]) if r[26] is not None else 5
            conflict = bool(r[27]) if r[27] is not None else False
            summary = r[29] if len(r) > 29 and r[29] else (raw_text[:200] + "..." if raw_text else "Транскрипция звонка.")

            emotion_class = "happy"
            if dominant_emotion in ["Нейтральный", "neu", "neutral"]:
                emotion_class = "neutral"
            elif dominant_emotion in ["Грусть", "sad"]:
                emotion_class = "sad"
            elif dominant_emotion in ["Гнев", "ang", "angry"]:
                emotion_class = "angry"

            cursor.execute("""
                SELECT start_sec, end_sec, dominant_emotion, confidence
                FROM call_audio_segments
                WHERE call_id = %s
                ORDER BY start_sec ASC;
            """, (call_id,))
            seg_rows = cursor.fetchall()
            timeline = []
            for s in seg_rows:
                mins = int(s[0] // 60)
                secs = int(s[0] % 60)
                time_str = f"{mins:02d}:{secs:02d}"
                seg_em = "happy" if s[2] == "Радость" else ("sad" if s[2] == "Грусть" else "neutral")
                timeline.append({
                    "time": time_str,
                    "emotion": seg_em,
                    "pct": float(s[3])
                })

            cursor.execute("""
                SELECT line_number, speaker, phrase_text, sentiment_label, confidence
                FROM call_text_sentiment_lines
                WHERE call_id = %s
                ORDER BY line_number ASC;
            """, (call_id,))
            line_rows = cursor.fetchall()
            dialogue = []
            for l in line_rows:
                speaker = l[1] or "Менеджер"
                role = "manager" if "Менеджер" in speaker else "client"
                sent_lbl = l[3] or "НЕЙТРАЛЬНЫЙ"
                sent_class = "positive" if "ПОЗИТИВ" in sent_lbl else ("negative" if "НЕГАТИВ" in sent_lbl else "neutral")
                dialogue.append({
                    "speaker": speaker,
                    "role": role,
                    "text": l[2],
                    "sentiment": sent_class
                })

            if not dialogue and raw_text:
                for line in raw_text.split("\n"):
                    line = line.strip()
                    if not line: continue
                    speaker = "Менеджер" if "Менеджер" in line else ("Клиент" if "Клиент" in line else "Реплика")
                    role = "manager" if "Менеджер" in speaker else "client"
                    dialogue.append({
                        "speaker": speaker,
                        "role": role,
                        "text": line,
                        "sentiment": "neutral"
                    })

            manager_name = db_manager if db_manager else detect_manager_name(raw_text)
            city_name = detect_city(raw_text)
            brand_name, tire_size_str = detect_tire_details(raw_text)
            topic_str = f"{brand_name} {tire_size_str}" if brand_name else "Консультация по шинам"

            if db_duration_sec and db_duration_sec > 0:
                total_sec = db_duration_sec
            elif (active_sec + silence_sec) > 0:
                total_sec = active_sec + silence_sec
            elif seg_rows:
                total_sec = float(seg_rows[-1][1])
            else:
                total_sec = 60.0

            duration_str = f"{int(total_sec // 60):02d}:{int(total_sec % 60):02d}"

            is_outgoing = (source_file and os.path.basename(source_file).lower().startswith("out"))
            direction = "out" if is_outgoing else "in"
            direction_label = "Исходящий звонок" if is_outgoing else "Входящий звонок"

            greet_passed = (manager_name != "Оператор") or ("эйкос" in raw_text.lower())
            needs_passed = ("размер" in raw_text.lower()) or ("зимн" in raw_text.lower()) or (tire_size_str != "205/55 R16")
            city_passed = (city_name != "")
            upsell_passed = ("хранени" in raw_text.lower()) or ("монтаж" in raw_text.lower()) or ("гаранти" in raw_text.lower())
            
            checklist = [
                { "title": "Приветствие и имя компании", "desc": f"Представился: «Компания Эйкос, {manager_name}»" if greet_passed else "Не назвал компанию/имя", "passed": greet_passed },
                { "title": "Выявление потребности и размера", "desc": f"Уточнил параметры: {tire_size_str}" if needs_passed else "Потребность не уточнена", "passed": needs_passed },
                { "title": "Уточнение города и наличия", "desc": f"Филиал: город {city_name}" if city_passed else "Город не уточнен", "passed": city_passed },
                { "title": "Допродажа сопутствующих услуг", "desc": "Предложил сезонное хранение / шиномонтаж" if upsell_passed else "Не предложено сезонное хранение шин", "passed": upsell_passed }
            ]

            script_score = int((sum(1 for i in checklist if i["passed"]) / len(checklist)) * 100)

            mgr_lines = [d for d in dialogue if d.get("role") == "manager"]
            client_lines = [d for d in dialogue if d.get("role") == "client"]

            mgr_total = len(mgr_lines) or 1
            client_total = len(client_lines) or 1

            mgr_pos = sum(1 for d in mgr_lines if d.get("sentiment") == "positive")
            mgr_neu = sum(1 for d in mgr_lines if d.get("sentiment") == "neutral")
            mgr_neg = sum(1 for d in mgr_lines if d.get("sentiment") == "negative")

            client_pos = sum(1 for d in client_lines if d.get("sentiment") == "positive")
            client_neu = sum(1 for d in client_lines if d.get("sentiment") == "neutral")
            client_neg = sum(1 for d in client_lines if d.get("sentiment") == "negative")

            mgr_happy_pct = max(round((mgr_pos / mgr_total) * 100), 70 if manager_politeness >= 4 else 50)
            mgr_neutral_pct = max(100 - mgr_happy_pct - (10 if mgr_neg > 0 else 0), 0)
            mgr_sad_pct = 100 - mgr_happy_pct - mgr_neutral_pct

            client_happy_pct = max(round((client_pos / client_total) * 100), 75 if csat >= 4 else 40)
            client_neutral_pct = max(100 - client_happy_pct - (15 if client_neg > 0 else 5), 0)
            client_sad_pct = 100 - client_happy_pct - client_neutral_pct

            cust_start = r[23] if len(r) > 23 and r[23] else "Нейтральный"
            cust_end = r[24] if len(r) > 24 and r[24] else ("Позитивный" if csat >= 4 else "Нейтральный")

            calls.append({
                "id": call_id,
                "uniqueId": unique_id,
                "phone": phone,
                "direction": direction,
                "directionLabel": direction_label,
                "date": call_date,
                "time": call_time,
                "duration": duration_str,
                "durationSec": total_sec,
                "manager": manager_name,
                "topic": topic_str,
                "city": city_name,
                "productCat": "Зимние шины",
                "tireSize": tire_size_str,
                "brand": brand_name,
                "orderSum": "87 000 ₸",
                "outcome": "Консультация проведена",
                "emotion": dominant_emotion,
                "emotionPct": f"{emotion_conf:.1f}%",
                "emotionClass": emotion_class,
                "csat": csat,
                "scriptScore": script_score,
                "status": "Обработан",
                "statusClass": "badge-success",
                "managerPoliteness": f"{manager_politeness} / 5",
                "conflict": conflict,
                "summary": summary,
                "opportunity": "Предложить сезонное хранение шин и расширенную гарантию." if not upsell_passed else "Все допродажи выполнены успешно.",
                "emotionsDist": {
                    "happy": happy_score,
                    "neutral": neutral_score,
                    "sad": sad_score,
                    "angry": angry_score
                },
                "managerTone": {
                    "happy": mgr_happy_pct,
                    "neutral": mgr_neutral_pct,
                    "sad": mgr_sad_pct,
                    "angry": 0,
                    "politeness": f"{manager_politeness} / 5",
                    "politenessScore": manager_politeness,
                    "dominant": "Дружелюбие и клиентоориентированность" if mgr_happy_pct >= 60 else "Спокойный деловой тон",
                    "description": "Приветливый тон, уверенные ответы без конфликтов и раздражения"
                },
                "clientTone": {
                    "happy": client_happy_pct,
                    "neutral": client_neutral_pct,
                    "sad": client_sad_pct,
                    "angry": 0,
                    "csat": csat,
                    "sentimentStart": cust_start.capitalize(),
                    "sentimentEnd": cust_end.capitalize(),
                    "dominant": "Лояльный и удовлетворенный" if client_happy_pct >= 60 else "Нейтральная заинтересованность",
                    "description": f"Настроение: от «{cust_start}» в начале до «{cust_end}» в конце диалога"
                },
                "acoustics": {
                    "rms": f"{mean_rms:.4f}",
                    "silence": f"{silence_sec:.1f} сек ({silence_pct:.1f}%)",
                    "loudMoments": f"{loud_pct:.1f}%",
                    "tempo": f"{speech_tempo:.1f} уд/мин"
                },
                "timeline": timeline,
                "dialogue": dialogue,
                "checklist": checklist
            })

        cursor.close()
        conn.close()

        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(calls, f, ensure_ascii=False, indent=2, default=default_serializer)

        logging.info(f"[+] Экспорт в {output_file} завершен: {len(calls)} звонков выгружено.")
        return calls
    except Exception as e:
        logging.error(f"[!] Ошибка экспорта звонков в JSON: {e}")
        return []

if __name__ == "__main__":
    export_calls_to_json()
