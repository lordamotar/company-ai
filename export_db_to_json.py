import os
import json
import psycopg2
import logging
import re
from datetime import date, time, datetime

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "company"),
    "user": os.getenv("DB_USER", "company_user"),
    "password": os.getenv("DB_PASSWORD", "company_password"),
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
    names = [
        "Диас", "Фарид", "Рустем", "Нуржан", "Жандос", "Владислав", 
        "Руслан", "Азамат", "Данияр", "Айбек", "Ербол", "Максим", 
        "Алексей", "Ерасыл", "Айнур"
    ]
    first_block = text[:300]
    for n in names:
        if re.search(rf'\b{n}\b', first_block, re.I):
            return n

    return "Оператор"

def detect_city(text):
    text_lower = (text or "").lower()
    cities = ["Алматы", "Астана", "Шымкент", "Караганда", "Актобе", "Тараз", "Павлодар", "Усть-Каменогорск", "Семей", "Атырау", "Костанай", "Кызылорда", "Уральск", "Петропавловск", "Актау"]
    for c in cities:
        if c.lower() in text_lower:
            return c
    return None

def detect_season(text):
    text_lower = (text or "").lower()
    if "нешип" in text_lower or "не шип" in text_lower or "липуч" in text_lower or "фрикцион" in text_lower:
        return "Зимние фрикционные (липучка)"
    elif "шип" in text_lower or "шипован" in text_lower:
        return "Зимние шипованные"
    elif "всесезон" in text_lower:
        return "Всесезонные"
    elif "летн" in text_lower or "лето" in text_lower:
        return "Летние"
    elif "зимн" in text_lower or "зима" in text_lower:
        return "Зимние"
    return None

def detect_tire_details(text):
    text_str = text or ""
    brands = [
        "Cordiant", "Winter Drive", "Nokian", "Nordman", "Kormoran", "Michelin", 
        "Pirelli", "Bridgestone", "Continental", "Viatti", "Triangle", "Sailun", 
        "Кама", "Dunlop", "Yokohama", "Goodyear", "Hankook", "Kumho", "BFGoodrich", "Skad"
    ]
    found_brands = [b for b in brands if b.lower() in text_str.lower()]
    brand_str = " / ".join(found_brands) if found_brands else None

    m_size = re.search(r'(\d{3}[\s/]\d{2}[\s/R]*\d{2})', text_str, re.I)
    tire_size = m_size.group(1).replace(" ", "/").upper() if m_size else None

    return brand_str, tire_size

def detect_call_classification(text, sale_status="Консультация", service_booking="Нет", conflict=False, has_upsell=False):
    """Определяет категорию, подкатегорию и теги для звонка на основе текста и метаданных."""
    text_lower = (text or "").lower()
    tags = []

    # Теги
    if any(k in text_lower for k in ["kaspi", "каспи", "рассрочк", "кредит", "qr"]):
        tags.append("Kaspi / Рассрочка")
    if any(k in text_lower for k in ["доставк", "отправк"]):
        tags.append("Доставка")
    if has_upsell or any(k in text_lower for k in ["хранени", "доп продаж"]):
        tags.append("Доп. продажа")
    if conflict or "жалоб" in text_lower or "претензи" in text_lower:
        tags.append("Конфликт / Претензия")
    if "наличи" in text_lower or "есть в" in text_lower:
        tags.append("Проверка наличия")

    # Категории
    if service_booking == "Да" or ("монтаж" in text_lower and ("запис" in text_lower or "приехать" in text_lower or "поменять" in text_lower)):
        category = "Запись на шиномонтаж"
        subcategory = "Сервисное обслуживание"
    elif "забронир" in text_lower or "резерв" in text_lower or sale_status in ["Продажа", "Заказ", "Забронировано"] or ("комплект" in text_lower and "наличи" in text_lower):
        category = "Подбор и бронь шин"
        subcategory = "Заказ и бронирование"
    elif any(k in text_lower for k in ["адрес", "где вы", "как проехать", "улиц", "до скольки", "работает", "график"]):
        category = "Инфо: Адрес / Время"
        subcategory = "Справочная информация"
    elif any(k in text_lower for k in ["интернет-заказ", "номер заказа", "пришли шины", "готовы шины"]):
        category = "Статус заказа"
        subcategory = "Проверка готовности"
    elif any(k in text_lower for k in ["гарантия", "брак", "порез", "возврат", "претензия"]):
        category = "Рекламация / Претензия"
        subcategory = "Гарантия и Возврат"
    else:
        category = "Прочее / Консультация"
        subcategory = "Общие вопросы"

    return category, subcategory, tags

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_OUTPUT_JSON = os.path.join(BASE_DIR, "frontend", "calls_data.json")
DEFAULT_AUDIT_JSON = os.path.join(BASE_DIR, "frontend", "audit_data.json")

def export_audit_logs_to_json(output_file=DEFAULT_AUDIT_JSON, write_to_file=True):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 
                l.id, l.call_id, l.field_name, l.old_value, l.new_value, 
                l.changed_by, l.changed_at, l.comment,
                t.phone, t.call_date, t.call_time, t.manager_name
            FROM call_audit_logs l
            LEFT JOIN call_transcriptions t ON l.call_id = t.id
            ORDER BY l.id DESC;
        """)
        rows = cursor.fetchall()
        logs = []
        for r in rows:
            changed_time = r[6].strftime("%d.%m.%Y %H:%M:%S") if r[6] else "—"
            field_ru = "Менеджер" if r[2] == "manager_name" else ("Город / Филиал" if r[2] == "city_name" else r[2])
            logs.append({
                "id": r[0],
                "callId": r[1],
                "fieldName": r[2],
                "fieldLabel": field_ru,
                "oldValue": r[3] or "—",
                "newValue": r[4] or "—",
                "changedBy": r[5] or "Супервайзер",
                "changedAt": changed_time,
                "comment": r[7] or "Корректировка данных",
                "phone": r[8] or "—",
                "callDate": r[9].strftime("%d.%m.%Y") if r[9] else "—",
                "callTime": r[10].strftime("%H:%M:%S") if r[10] else "—",
                "currentManager": r[11] or "Оператор"
            })

        cursor.close()
        conn.close()

        if write_to_file and output_file:
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(logs, f, ensure_ascii=False, indent=2, default=default_serializer)
            logging.info(f"[+] Экспорт аудита в {output_file} завершен: {len(logs)} записей.")
        return logs
    except Exception as e:
        logging.error(f"[!] Ошибка экспорта журнала аудита: {e}")
        return []

def export_calls_to_json(output_file=DEFAULT_OUTPUT_JSON, write_to_file=True):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 
                t.id, t.unique_id, t.phone, t.call_date, t.call_time, 
                t.text, t.source_file, t.status, t.manager_name, t.city_name, t.duration_sec,
                af.mean_rms, af.max_rms, af.loud_moments_pct, af.silent_duration_sec, af.silence_pct, af.speech_tempo, af.active_speech_sec,
                ae.dominant_emotion, ae.confidence as emotion_conf, ae.happy_score, ae.neutral_score, ae.sad_score, ae.angry_score,
                cs.customer_sentiment_start, cs.customer_sentiment_end, cs.satisfaction_score, cs.manager_politeness, cs.conflict_detected, cs.conflict_reason, cs.tone_summary,
                t.sale_status, t.order_sum, t.has_upsell, t.upsell_sum, t.upsell_text, t.service_booking, t.service_date,
                t.client_name, t.car_model, t.tire_brand, t.tire_size, t.tire_season, t.tire_count, t.branch_address, t.payment_method, t.lost_reason, t.call_summary,
                t.call_category, t.call_subcategory, t.call_tags, t.checklist
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
            db_city = r[9]
            db_duration_sec = float(r[10]) if r[10] is not None else None

            mean_rms = float(r[11]) if r[11] is not None else 0.04
            max_rms = float(r[12]) if r[12] is not None else 0.2
            loud_pct = float(r[13]) if r[13] is not None else 0.0
            silence_sec = float(r[14]) if r[14] is not None else 0.0
            silence_pct = float(r[15]) if r[15] is not None else 20.0
            speech_tempo = float(r[16]) if r[16] is not None else 130.0
            active_sec = float(r[17]) if r[17] is not None else 0.0

            dominant_emotion = r[18] or "Не определено"
            emotion_conf = float(r[19]) if r[19] is not None else 0.0
            happy_score = float(r[20]) if r[20] is not None else 0.0
            neutral_score = float(r[21]) if r[21] is not None else 0.0
            sad_score = float(r[22]) if r[22] is not None else 0.0
            angry_score = float(r[23]) if r[23] is not None else 0.0

            csat = int(r[26]) if r[26] is not None else None
            manager_politeness = int(r[27]) if r[27] is not None else None
            conflict = bool(r[28]) if r[28] is not None else False
            summary = r[30] if len(r) > 30 and r[30] else (raw_text[:200] + "..." if raw_text else "Транскрипция звонка.")

            sale_status = r[31] if len(r) > 31 and r[31] else "Консультация"
            order_sum = r[32] if len(r) > 32 and r[32] else "0 ₸"
            has_upsell = bool(r[33]) if len(r) > 33 and r[33] is not None else False
            upsell_sum = r[34] if len(r) > 34 and r[34] else "0 ₸"
            upsell_text = r[35] if len(r) > 35 and r[35] else ""
            service_booking = r[36] if len(r) > 36 and r[36] else "Нет"
            service_date = r[37] if len(r) > 37 and r[37] else ""

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

            db_client_name = r[38] if len(r) > 38 and r[38] else None
            db_car_model = r[39] if len(r) > 39 and r[39] else None
            db_tire_brand = r[40] if len(r) > 40 and r[40] else None
            db_tire_size = r[41] if len(r) > 41 and r[41] else None
            db_tire_season = r[42] if len(r) > 42 and r[42] else None
            db_tire_count = r[43] if len(r) > 43 and r[43] else None
            db_branch_address = r[44] if len(r) > 44 and r[44] else None
            db_payment_method = r[45] if len(r) > 45 and r[45] else None
            db_lost_reason = r[46] if len(r) > 46 and r[46] else None
            db_call_summary = r[47] if len(r) > 47 and r[47] else None
            db_category = r[48] if len(r) > 48 and r[48] else None
            db_subcategory = r[49] if len(r) > 49 and r[49] else None
            db_tags = r[50] if len(r) > 50 and r[50] else None
            db_checklist = r[51] if len(r) > 51 and r[51] else None
            if isinstance(db_checklist, str):
                try:
                    db_checklist = json.loads(db_checklist)
                except Exception:
                    db_checklist = None

            # Если классификация еще не установлена в БД, высчитываем ее
            if not db_category or db_category == 'Прочее / Консультация':
                det_cat, det_subcat, det_tags = detect_call_classification(raw_text, sale_status, service_booking, conflict, has_upsell)
                call_category = db_category or det_cat
                call_subcategory = db_subcategory or det_subcat
                call_tags = db_tags if db_tags else det_tags
            else:
                call_category = db_category
                call_subcategory = db_subcategory or "Общие вопросы"
                call_tags = db_tags if isinstance(db_tags, list) else []

            manager_name = db_manager if db_manager else detect_manager_name(raw_text)
            city_name = db_city if db_city else detect_city(raw_text)
            det_brand, det_size = detect_tire_details(raw_text)
            brand_name = db_tire_brand or det_brand
            tire_size_str = db_tire_size or det_size
            topic_str = f"{brand_name} {tire_size_str}" if brand_name else "Консультация по шинам"
            client_name = db_client_name or (f"Клиент (+..{phone[-4:]})" if phone and len(phone) >= 4 else "Клиент")

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

            text_lower = raw_text.lower()
            greet_passed = bool(manager_name and manager_name not in ["Оператор", "Не определен"]) or any(k in text_lower for k in ["эйкос", "компания эйкос", "добрый день", "здравствуйте", "доброе утро"])
            needs_passed = bool(tire_size_str) or any(k in text_lower for k in ["какой размер", "какой радиус", "какой автомобиль", "какая машина", "шипы или", "сезон"])
            present_passed = bool(brand_name) and any(k in text_lower for k in ["тенге", "стоит", "цена", "тысяч", "вариант", "предложить", "в наличии"])
            objection_passed = (not conflict) and any(k in text_lower for k in ["предлож", "склад", "филиал", "вариант", "можем", "доставк", "посмотр"])
            closing_passed = any(k in text_lower for k in ["до свидания", "всего доброго", "хорошего дня", "всего хорошего", "до встречи", "обращайтесь"])
            prof_passed = ((manager_politeness is not None and manager_politeness >= 4) or (manager_politeness is None and not conflict)) and (angry_score <= 15)
            upsell_passed = has_upsell or any(k in text_lower for k in ["хранени", "шиномонтаж", "монтаж", "балансировк", "диск", "датчик", "пакет", "гаранти"])

            if db_checklist and isinstance(db_checklist, list) and len(db_checklist) > 0:
                checklist = []
                for item in db_checklist:
                    title = item.get("title") or item.get("key", "Критерий")
                    quote = str(item.get("quote", "")).strip()
                    comment = str(item.get("comment", "")).strip()
                    if quote:
                        desc = f"Цитата: «{quote}» • {comment}" if comment else f"Цитата: «{quote}»"
                    else:
                        desc = comment or ("Выполнено" if item.get("passed") else "Не выполнено")
                    checklist.append({
                        "title": title,
                        "desc": desc,
                        "passed": bool(item.get("passed", False))
                    })
            else:
                checklist = [
                    { "title": "Приветствие", "desc": f"Представился: «Компания Эйкос, {manager_name}»" if greet_passed else "Не назвал компанию / не представился", "passed": greet_passed },
                    { "title": "Выявления потребностей", "desc": f"Уточнил параметры и типоразмер: {tire_size_str or 'запрошен клиентом'}" if needs_passed else "Потребность и типоразмер не уточнены", "passed": needs_passed },
                    { "title": "Презентация", "desc": f"Презентовал варианты шин ({brand_name or 'в наличии'}), озвучил стоимость" if present_passed else "Презентация вариантов шин не проведена", "passed": present_passed },
                    { "title": "Работа с возражениями", "desc": "Отработал вопросы и сомнения по наличию/складу, предложил альтернативы" if objection_passed else "Не отработаны сомнения/претензия клиента", "passed": objection_passed },
                    { "title": "Окончание разговора", "desc": "Вежливо попрощался и подвел итог" if closing_passed else "Не зафиксирован итог / нет вежливого прощания", "passed": closing_passed },
                    { "title": "Профессиональный подход", "desc": f"Оценка вежливости {manager_politeness}/5, соблюдение делового этикета" if (manager_politeness and prof_passed) else ("Соблюдение делового этикета" if prof_passed else "Зафиксированы замечания к культуре речи"), "passed": prof_passed },
                    { "title": "Доп прдажа", "desc": "Предложил сопутствующие услуги (хранение шин / шиномонтаж)" if upsell_passed else "Не предложены сопутствующие услуги (хранение/монтаж)", "passed": upsell_passed }
                ]

            script_score = int((sum(1 for i in checklist if i["passed"]) / len(checklist)) * 100)

            mgr_lines = [d for d in dialogue if d.get("role") == "manager"]
            client_lines = [d for d in dialogue if d.get("role") == "client"]

            mgr_total = len(mgr_lines)
            client_total = len(client_lines)

            mgr_pos = sum(1 for d in mgr_lines if d.get("sentiment") == "positive")
            mgr_neu = sum(1 for d in mgr_lines if d.get("sentiment") == "neutral")
            mgr_neg = sum(1 for d in mgr_lines if d.get("sentiment") == "negative")

            client_pos = sum(1 for d in client_lines if d.get("sentiment") == "positive")
            client_neu = sum(1 for d in client_lines if d.get("sentiment") == "neutral")
            client_neg = sum(1 for d in client_lines if d.get("sentiment") == "negative")

            # Честный расчет процентного соотношения реплик без искусственного завышения
            if client_total > 0:
                client_sad_pct = round((client_neg / client_total) * 100) if client_neg > 0 else 0
                client_happy_pct = round((client_pos / client_total) * 100) if client_pos > 0 else 0
                client_neutral_pct = max(100 - client_happy_pct - client_sad_pct, 0)
            else:
                client_sad_pct = 0
                client_happy_pct = 0
                client_neutral_pct = 100

            if mgr_total > 0:
                mgr_sad_pct = round((mgr_neg / mgr_total) * 100) if mgr_neg > 0 else 0
                mgr_happy_pct = round((mgr_pos / mgr_total) * 100) if mgr_pos > 0 else 0
                mgr_neutral_pct = max(100 - mgr_happy_pct - mgr_sad_pct, 0)
            else:
                mgr_sad_pct = 0
                mgr_happy_pct = 0
                mgr_neutral_pct = 100

            cust_start = str(r[24]) if len(r) > 24 and r[24] is not None else "Не определено"
            cust_end = str(r[25]) if len(r) > 25 and r[25] is not None else "Не определено"

            season_val = db_tire_season or detect_season(raw_text) or "Не указано"

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
                "clientName": client_name,
                "carModel": db_car_model or "Не указано",
                "tireBrand": brand_name,
                "tireSize": tire_size_str,
                "tireSeason": season_val,
                "tireCount": db_tire_count or "4 шт.",
                "branchAddress": db_branch_address or (f"г. {city_name}" if city_name else "Филиал Aikos"),
                "paymentMethod": db_payment_method or "Оплата при получении",
                "lostReason": db_lost_reason or "",
                "callSummary": db_call_summary or summary,
                "topic": topic_str,
                "city": city_name or "Не указан",
                "callCategory": call_category,
                "callSubcategory": call_subcategory,
                "callTags": call_tags,
                "productCat": season_val,
                "orderSum": order_sum,
                "outcome": sale_status,
                "saleStatus": sale_status,
                "hasUpsell": has_upsell,
                "upsellSum": upsell_sum,
                "upsellText": upsell_text,
                "serviceBooking": service_booking,
                "serviceDate": service_date,
                "emotion": dominant_emotion,
                "emotionPct": f"{emotion_conf:.1f}%",
                "emotionClass": emotion_class,
                "csat": csat,
                "scriptScore": script_score,
                "status": "Обработан",
                "statusClass": "badge-success",
                "managerPoliteness": f"{manager_politeness} / 5" if manager_politeness is not None else "Не оценено",
                "conflict": conflict,
                "conflictReason": r[29] if len(r) > 29 and r[29] else ("Клиент выразил недовольство условиями" if conflict else None),
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
                    "politeness": f"{manager_politeness} / 5" if manager_politeness is not None else "Не оценено",
                    "politenessScore": manager_politeness,
                    "dominant": "Дружелюбие и клиентоориентированность" if mgr_happy_pct >= 50 else ("Спокойный деловой тон" if mgr_neutral_pct >= 50 else "Сдержанный тон"),
                    "description": "Приветливый тон, уверенные ответы без конфликтов" if not conflict else "Зафиксировано напряжение в диалоге"
                },
                "clientTone": {
                    "happy": client_happy_pct,
                    "neutral": client_neutral_pct,
                    "sad": client_sad_pct,
                    "angry": 0,
                    "csat": csat,
                    "sentimentStart": cust_start.capitalize(),
                    "sentimentEnd": cust_end.capitalize(),
                    "dominant": "Лояльный и удовлетворенный" if client_happy_pct >= 50 else ("Нейтральная заинтересованность" if client_neutral_pct >= 50 else "Сдержанный/настороженный"),
                    "description": f"Настроение: от «{cust_start}» в начале до «{cust_end}» в конце диалога" if cust_start != "Не определено" else "Тон не анализировался"
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

            # Синхронизируем разметку обратно в PostgreSQL для сохранности
            try:
                cursor.execute("""
                    UPDATE call_transcriptions
                    SET call_category = COALESCE(call_category, %s),
                        call_subcategory = COALESCE(call_subcategory, %s),
                        call_tags = COALESCE(call_tags, %s),
                        tire_brand = COALESCE(NULLIF(tire_brand, ''), %s),
                        tire_size = COALESCE(NULLIF(tire_size, ''), %s)
                    WHERE id = %s;
                """, (call_category, call_subcategory, json.dumps(call_tags, ensure_ascii=False), det_brand, det_size, call_id))
            except Exception:
                pass

        conn.commit()
        cursor.close()
        conn.close()

        if write_to_file and output_file:
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(calls, f, ensure_ascii=False, indent=2, default=default_serializer)
            logging.info(f"[+] Экспорт в {output_file} завершен: {len(calls)} звонков выгружено.")

            # Экспортируем также журнал аудита
            try:
                export_audit_logs_to_json(write_to_file=True)
            except Exception as err:
                logging.warning(f"Не удалось обновить журнал аудита: {err}")

        return calls
    except Exception as e:
        logging.error(f"[!] Ошибка экспорта звонков в JSON: {e}")
        return []

if __name__ == "__main__":
    export_calls_to_json()
    export_audit_logs_to_json()
