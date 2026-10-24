#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIKOS CRM — Скрипт конвертации диалогов Bitrix24 в JSON и генерации помесячной аналитики.
Включает:
- Анализ времени ответа (первый ответ ИИ-бота, время реакции оператора после передачи).
- Аналитику после передачи: что происходит с лидом (продажа, сервис, клиент не дождался, оператор не ответил).
- Статусы CRM: стадии сделок и лидов Bitrix24.
- Полную таблицу всех диалогов с клиентской пагинацией, поиском и фильтрами.
- Экспорт в dialog/d-json/ и генерацию автономного интерактивного дашборда dashboard.html.
"""

import os
import sys
import re
import csv
import glob
import json
import logging
from datetime import datetime
from collections import Counter, defaultdict

try:
    from tire_sizes import _parse_one, normalize_tire_size
    TIRE_SIZES_AVAILABLE = True
except ImportError:
    TIRE_SIZES_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIALOG_DIR = os.path.join(BASE_DIR, "dialog")
D_JSON_DIR = os.path.join(DIALOG_DIR, "d-json")
D_JSON_MONTHLY_DIR = os.path.join(D_JSON_DIR, "monthly")
DASHBOARD_HTML_PATH = os.path.join(DIALOG_DIR, "dashboard.html")

CITIES = [
    "Алматы", "Астана", "Нур-Султан", "Караганда", "Шымкент", "Актобе", "Тараз", 
    "Павлодар", "Усть-Каменогорск", "Семей", "Атырау", "Костанай", "Кызылорда", 
    "Уральск", "Петропавловск", "Актау", "Темиртау", "Туркестан", "Кокшетау", 
    "Талдыкорган", "Экибастуз", "Рудный", "Жанаозен", "Балхаш", "Кентау"
]

BRANDS = [
    "Continental", "Nokian", "Nordman", "Pirelli", "Michelin", "Bridgestone", 
    "Cordiant", "Viatti", "Triangle", "Sailun", "Yokohama", "Goodyear", 
    "Hankook", "Kormoran", "Maxxis", "Linglong", "Кама", "Dunlop", "Kumho",
    "Nexen", "Toyo", "Barum", "Matador", "Gislaved", "Skad", "Trebl", "K&K", "Replay"
]

CAR_MODELS = [
    "Camry", "RAV4", "Corolla", "Prado", "Land Cruiser", "Highlander", "Yaris", "Avensis",
    "Accent", "Elantra", "Sonata", "Tucson", "Santa Fe", "Creta", "Palisade",
    "Rio", "Cerato", "K5", "Optima", "Sportage", "Sorento", "Carnival", "Seltos",
    "Cobalt", "Nexia", "Lacetti", "Gentra", "Malibu", "Tracker", "Tahoe", "Captiva",
    "Polo", "Golf", "Passat", "Tiguan", "Touareg",
    "Duster", "Logan", "Sandero", "Kaptur",
    "Lada", "Vesta", "Granta", "Largus", "Priora", "Niva",
    "Mazda 3", "Mazda 6", "CX-5", "CX-9",
    "Outlander", "Pajero", "Lancer", "ASX",
    "X-Trail", "Qashqai", "Patrol", "Juke", "Teana", "Almera",
    "BMW", "Mercedes", "Audi", "Lexus", "Subaru", "Forester", "Outback",
    "Chery", "Haval", "Geely", "Changan", "Exeed", "Tank", "Zeekr", "Li Auto", "BYD"
]

def clean_tire_size(raw_size):
    """Валидирует типоразмер с отсечением телефонных номеров и мусора."""
    if TIRE_SIZES_AVAILABLE:
        norm = _parse_one(raw_size)
        if norm:
            return norm
    m = re.match(r'^(\d{3})[/\s\-xX]*(\d{2})[/\s\-xXRr]*(\d{2}(?:\.5)?)$', raw_size.strip())
    if m:
        w, p, r = int(m.group(1)), int(m.group(2)), float(m.group(3))
        if 135 <= w <= 355 and 25 <= p <= 85 and 12 <= r <= 24:
            return f"{w}/{p} R{int(r) if r.is_integer() else r}"
    return None

def extract_tire_sizes(text):
    sizes = set()
    matches = re.findall(r'\b(\d{3}[\s/xX\-]\d{2}[\s/xXRr]*\d{2}(?:\.5)?)\b', text)
    for m in matches:
        clean = re.sub(r'[\s\-xX]', '/', m)
        clean = re.sub(r'/+R?', '/', clean, flags=re.I)
        parts = clean.split('/')
        if len(parts) >= 3:
            raw = f"{parts[0]}/{parts[1]} R{parts[2]}"
            valid = clean_tire_size(raw)
            if valid:
                sizes.add(valid)

    if not sizes:
        disk_matches = re.findall(r'\b[RР]\s*(\d{2})\b', text, re.I)
        for d in disk_matches:
            val = int(d)
            if 13 <= val <= 22:
                sizes.add(f"Диски R{val}")

    return list(sizes)

def extract_city(text, messages):
    client_text = " ".join([m["text"] for m in messages if m["role"] == "client"])
    for city in CITIES:
        pattern = rf'\b{city}\b'
        if re.search(pattern, client_text, re.I):
            return "Астана" if city == "Нур-Султан" else city
    for city in CITIES:
        pattern = rf'(?:в|по|город|г\.)\s+{city}\b|\b{city}\b'
        if re.search(pattern, text, re.I):
            return "Астана" if city == "Нур-Султан" else city
    return "Не определен"

def extract_brands(text):
    found = set()
    for brand in BRANDS:
        if re.search(rf'\b{brand}\b', text, re.I):
            found.add(brand)
    return list(found)

def extract_car_model(text):
    for car in CAR_MODELS:
        if re.search(rf'\b{car}\b', text, re.I):
            return car
    return None

def extract_product_category(text, tire_sizes, brands):
    text_lower = text.lower()
    if any(w in text_lower for w in ["диск", "skad", "trebl", "литые"]):
        return "Диски"
    if any(w in text_lower for w in ["шип", "зимн", "липучк", "ice", "winter"]):
        return "Зимние шины"
    if any(w in text_lower for w in ["летн", "summer", "лето"]):
        return "Летние шины"
    if any(w in text_lower for w in ["хранен", "монтаж", "балансировк", "запис"]):
        return "Сервис / Услуги"
    if tire_sizes or brands:
        return "Шины (Общее)"
    return "Общая консультация"

def extract_intents(text):
    text_lower = text.lower()
    intents = []
    if any(w in text_lower for w in ["наличи", "есть в", "остаток", "склад", "в наличии"]):
        intents.append("Наличие на складе")
    if any(w in text_lower for w in ["цен", "стоим", "почем", "сколько стоит", "тенге", "₸", "скидк"]):
        intents.append("Стоимость / Цены")
    if any(w in text_lower for w in ["подобрать", "подберите", "подойдет", "какой размер", "на марку", "машин"]):
        intents.append("Подбор по авто")
    if any(w in text_lower for w in ["год", "страна", "производств", "производитель", "свежие"]):
        intents.append("Год / Страна производства")
    if any(w in text_lower for w in ["доставк", "отправк", "самовывоз", "адрес", "где забрать", "филиал"]):
        intents.append("Доставка / Самовывоз")
    if any(w in text_lower for w in ["запис", "монтаж", "переобуть", "хранени", "записаться"]):
        intents.append("Запись на сервис / Хранение")
    if not intents:
        intents.append("Консультация по ассортименту")
    return intents

def extract_source(messages):
    client_messages = [m["text"] for m in messages if m["role"] == "client"]
    client_text = " ".join(client_messages).lower() if client_messages else ""
    if not client_text:
        return "WhatsApp (Прямой)"
    if re.search(r'2gis|2гис|двагис|2\s*гис|2-гис|в\s+2\s*гисе|на\s+картах\s+2гис|приложени[яе]\s+2гис', client_text):
        return "2GIS (Карты)"
    if re.search(r'яндекс\s*карт|яндекс\s*навигатор|обращение\s+из\s+яндекс', client_text):
        return "Яндекс Карты"
    if re.search(r'kolesa\.kz|колеса\.кз|колёса\.кз|на\s+колесах|в\s+колесах|объявлени[еи]\s+на\s+колес', client_text):
        return "Kolesa.kz"
    if re.search(r'в\s+инстаграм|в\s+инсте|из\s+инстаграм|директ\s+инста|профиль\s+инста', client_text):
        return "Instagram"
    if re.search(r'на\s+сайте|с\s+сайта|в\s+интернет[- ]магазине|заказ\s+с\s+сайта|нашел\s+на\s+сайте|корзин[ае]\s+на\s+сайте|aikos\.kz', client_text):
        return "Сайт Aikos.kz"
    if re.search(r'в\s+каспи|из\s+каспи|каспи\s+магазин|в\s+магазине\s+каспи', client_text):
        return "Kaspi Магазин"
    if re.search(r'посоветова(?:л|ли)|по\s+рекомендаци|знакомы(?:й|е)\s+посоветова|брат\s+дал\s+номер|друг\s+посоветова', client_text):
        return "Рекомендация"
    return "WhatsApp (Прямой)"

def detect_language(messages):
    kz_markers = ["сәлеметсіз", "саламатсыз", "рахмет", "қанша", "бағасы", "бар ма", "дөңгелек", "қысқы", "жазғы", "керек"]
    client_text = " ".join([m["text"] for m in messages if m["role"] == "client"]).lower()
    return "Казахский" if any(w in client_text for w in kz_markers) else "Русский"

def extract_detailed_outcome(text, messages, row, operator_present):
    text_lower = text.lower()
    deal = str(row.get("Сделка") or "").strip()
    deal_id = str(row.get("Сделка - ID") or "").strip()
    
    is_service_booked = (
        bool(re.search(r'записал(?:и|а)?\s+вас', text_lower)) or
        bool(re.search(r'запись\s+на\s+(?:шиномонтаж|сервис|установку|балансировку|замену|хранение|геометрию|развал)', text_lower)) or
        bool(re.search(r'будем\s+ждать\s+вас\s+на\s+шиномонтаж', text_lower)) or
        bool(re.search(r'время\s+записи\s+подтвержден', text_lower))
    )
    
    is_sale_completed = (
        bool(deal or deal_id) or
        bool(re.search(r'номер\s+брони', text_lower)) or
        bool(re.search(r'заказ\s+(?:№|#|номер)\s*\d+', text_lower)) or
        bool(re.search(r'счет\s+на\s+оплату', text_lower)) or
        bool(re.search(r'выставил(?:а)?\s+счет', text_lower)) or
        bool(re.search(r'чек\s+об\s+оплат', text_lower)) or
        bool(re.search(r'оплатил[аи]?', text_lower)) or
        bool(re.search(r'оплачен[оа]?', text_lower)) or
        bool(re.search(r'забронировал(?:и|а)?', text_lower)) or
        bool(re.search(r'оформил(?:и|а)?\s+заказ', text_lower)) or
        bool(re.search(r'спасибо\s+за\s+покупку', text_lower))
    )
    
    is_lost = any(w in text_lower for w in [
        "уже купил", "не надо", "спасибо не надо", "отмена заказа", 
        "уже не актуально", "дорого у вас", "нашел дешевле", "нет в наличии"
    ])
    
    if is_service_booked:
        return "Запись на сервис / Услугу", "service"
    if is_sale_completed:
        return "Продажа / Оформлен заказ", "sale"
    if is_lost:
        return "Отказ / Клиент ушел", "lost"
    if operator_present:
        if any(w in text_lower for w in ["хорошего дня", "всего доброго", "пожалуйста", "до свидания", "рады помочь"]):
            return "Консультация менеджера", "consultation"
        return "В работе у менеджера", "in_progress"
    return "Консультация AI-бота", "bot"

def parse_dialog_messages(raw_text):
    if not raw_text:
        return []
    pattern = re.compile(
        r'\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]\s+([^:\(\n]+)\s*\(([^)\n]+)\):\s*',
        re.MULTILINE
    )
    matches = list(pattern.finditer(raw_text))
    messages = []
    for i, match in enumerate(matches):
        time_str = match.group(1)
        sender = match.group(2).strip()
        role_raw = match.group(3).strip().lower()
        start_idx = match.end()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        clean_text = re.sub(r'\[URL\](.*?)\[/URL\]', r'\1', raw_text[start_idx:end_idx].strip())
        
        sender_lower = sender.lower()
        if "new" in sender_lower or "бот" in role_raw or "ассистент" in sender_lower:
            role = "bot"
        elif "оператор" in role_raw or "сотрудник" in role_raw or any(op in sender_lower for op in ["усманова", "жумадилов", "жаназаров", "нуртазанов", "ошлаков"]):
            role = "operator"
        else:
            role = "client"

        messages.append({
            "time": time_str,
            "sender": sender,
            "role": role,
            "text": clean_text
        })
    return messages

def compute_dialog_timings(messages, operator_present):
    """
    Рассчитывает время первого ответа бота, время реакции оператора после передачи,
    и классифицирует статус передачи.
    """
    client_msgs = [m for m in messages if m["role"] == "client"]
    bot_msgs = [m for m in messages if m["role"] == "bot"]
    op_msgs = [m for m in messages if m["role"] == "operator"]

    bot_first_sec = None
    op_wait_min = None
    transfer_status = "bot_only"
    transfer_status_label = "Только AI-бот"

    # 1. Время первого ответа бота
    if client_msgs and bot_msgs:
        try:
            t_client = datetime.strptime(client_msgs[0]["time"], "%Y-%m-%d %H:%M:%S")
            for bm in bot_msgs:
                t_b = datetime.strptime(bm["time"], "%Y-%m-%d %H:%M:%S")
                if t_b >= t_client:
                    diff = (t_b - t_client).total_seconds()
                    if 0 <= diff <= 1800:
                        bot_first_sec = round(diff, 1)
                        break
        except Exception:
            pass

    # 2. Время ответа оператора и статус передачи
    if operator_present:
        if op_msgs:
            try:
                t_op = datetime.strptime(op_msgs[0]["time"], "%Y-%m-%d %H:%M:%S")
                prior_times = []
                for m in messages:
                    if m["role"] in ("client", "bot"):
                        t_m = datetime.strptime(m["time"], "%Y-%m-%d %H:%M:%S")
                        if t_m <= t_op:
                            prior_times.append(t_m)
                
                if prior_times:
                    t_handoff = prior_times[-1]
                    diff_sec = max(0, (t_op - t_handoff).total_seconds())
                    op_wait_min = round(diff_sec / 60, 1)
                else:
                    op_wait_min = 0.0

                if op_wait_min <= 5:
                    transfer_status = "op_fast"
                    transfer_status_label = "Отвечено оперативно (≤5 мин)"
                elif op_wait_min <= 15:
                    transfer_status = "op_normal"
                    transfer_status_label = "Отвечено штатно (5-15 мин)"
                elif op_wait_min <= 60:
                    transfer_status = "op_slow"
                    transfer_status_label = "Отвечено с задержкой (15-60 мин)"
                else:
                    transfer_status = "op_critical"
                    transfer_status_label = "Критическая задержка (>1 ч)"
            except Exception:
                transfer_status = "op_normal"
                transfer_status_label = "Отвечено оператором"
        else:
            transfer_status = "op_no_reply"
            transfer_status_label = "Оператор НЕ ответил"
    else:
        transfer_status = "bot_only"
        transfer_status_label = "Без передачи (Только бот)"

    return bot_first_sec, op_wait_min, transfer_status, transfer_status_label

def determine_post_transfer(transfer_status, outcome_type, messages):
    """Определяет, что дальше произошло с обращением после передачи оператору."""
    if transfer_status == "bot_only":
        return "Закрыто ИИ-ботом"
    if transfer_status == "op_no_reply":
        return "Клиент брошен (нет ответа)"

    if outcome_type == "sale":
        return "Успешная продажа"
    elif outcome_type == "service":
        return "Запись на сервис"
    elif outcome_type == "lost":
        return "Отказ / Клиент ушел"

    if messages:
        last_role = messages[-1]["role"]
        if last_role == "operator":
            return "Клиент перестал отвечать"
        elif last_role == "client":
            return "Ждет ответа оператора"

    return "Консультация завершена"

def compute_metrics(dialogs):
    """Вычисляет сводные показатели для группы диалогов (за месяц или в целом)."""
    total = len(dialogs)
    if total == 0:
        return {}

    cities_counter = Counter(d.get("city", "Не определен") for d in dialogs)
    categories_counter = Counter(d.get("category", "Шины (Общее)") for d in dialogs)
    sources_counter = Counter(d.get("source", "WhatsApp (Прямой)") for d in dialogs)
    outcomes_counter = Counter(d.get("outcome", "Завершен диалог") for d in dialogs)
    transfer_status_counter = Counter(d.get("transferStatusLabel", "Только AI-бот") for d in dialogs)
    post_transfer_counter = Counter(d.get("postTransferAction", "Закрыто ИИ-ботом") for d in dialogs if d.get("operatorPresent"))
    deal_stages_counter = Counter(d.get("dealStage", "—") for d in dialogs if d.get("dealStage") and d.get("dealStage") != "—")
    lead_stages_counter = Counter(d.get("leadStage", "—") for d in dialogs if d.get("leadStage") and d.get("leadStage") != "—")

    sizes_counter = Counter()
    for d in dialogs:
        for s in d.get("tireSizes", []):
            sizes_counter[s] += 1

    brands_counter = Counter()
    for d in dialogs:
        for b in d.get("brands", []):
            brands_counter[b] += 1

    bot_only = sum(1 for d in dialogs if not d.get("operatorPresent"))
    operator_escalated = sum(1 for d in dialogs if d.get("operatorPresent"))
    tech_glitches = sum(d.get("techGlitchesCount", 0) for d in dialogs)
    dialogs_with_glitches = sum(1 for d in dialogs if d.get("techGlitchesCount", 0) > 0)
    sales = sum(1 for d in dialogs if d.get("isSale"))
    services = sum(1 for d in dialogs if d.get("isService"))
    total_conversions = sales + services

    # Расчет среднего времени ответа
    bot_times = [d["botResponseSec"] for d in dialogs if d.get("botResponseSec") is not None]
    avg_bot_time = round(sum(bot_times) / len(bot_times), 1) if bot_times else 0.0

    op_waits = [d["operatorWaitMin"] for d in dialogs if d.get("operatorWaitMin") is not None]
    avg_op_wait = round(sum(op_waits) / len(op_waits), 1) if op_waits else 0.0

    # Количество диалогов без ответа оператора
    op_no_reply_count = sum(1 for d in dialogs if d.get("transferStatus") == "op_no_reply")

    # Статистика по менеджерам (нагрузка, скорость, конверсия)
    manager_breakdown = defaultdict(lambda: {"total": 0, "sales": 0, "service": 0, "consult": 0, "no_reply": 0, "waits": []})
    for d in dialogs:
        if d.get("operatorPresent"):
            op = d.get("operator", "Оператор")
            manager_breakdown[op]["total"] += 1
            if d.get("transferStatus") == "op_no_reply":
                manager_breakdown[op]["no_reply"] += 1
            if d.get("operatorWaitMin") is not None:
                manager_breakdown[op]["waits"].append(d["operatorWaitMin"])
            if d.get("isSale"):
                manager_breakdown[op]["sales"] += 1
            elif d.get("isService"):
                manager_breakdown[op]["service"] += 1
            else:
                manager_breakdown[op]["consult"] += 1

    manager_perf = []
    for op, stats in sorted(manager_breakdown.items(), key=lambda x: x[1]["total"], reverse=True):
        cr = round(((stats["sales"] + stats["service"]) / stats["total"]) * 100, 1) if stats["total"] > 0 else 0
        avg_w = round(sum(stats["waits"]) / len(stats["waits"]), 1) if stats["waits"] else 0.0
        manager_perf.append({
            "name": op,
            "total": stats["total"],
            "sales": stats["sales"],
            "service": stats["service"],
            "consult": stats["consult"],
            "noReply": stats["no_reply"],
            "avgWaitMin": avg_w,
            "conversionRate": cr
        })

    return {
        "totalDialogs": total,
        "salesCount": sales,
        "salesPct": round((sales / total) * 100, 1),
        "serviceCount": services,
        "servicePct": round((services / total) * 100, 1),
        "totalConversions": total_conversions,
        "conversionRate": round((total_conversions / total) * 100, 1),
        "botOnlyCount": bot_only,
        "botOnlyPct": round((bot_only / total) * 100, 1),
        "operatorEscalatedCount": operator_escalated,
        "operatorEscalatedPct": round((operator_escalated / total) * 100, 1),
        "avgBotTimeSec": avg_bot_time,
        "avgOperatorWaitMin": avg_op_wait,
        "opNoReplyCount": op_no_reply_count,
        "opNoReplyPct": round((op_no_reply_count / operator_escalated) * 100, 1) if operator_escalated else 0.0,
        "techGlitchesTotal": tech_glitches,
        "dialogsWithGlitches": dialogs_with_glitches,
        "glitchRatePct": round((dialogs_with_glitches / total) * 100, 1),
        "totalMessages": sum(d.get("messagesCount", 0) for d in dialogs),
        "avgMessages": round(sum(d.get("messagesCount", 0) for d in dialogs) / total, 1),
        "transferStatuses": [{"label": k, "count": v, "pct": round((v / total) * 100, 1)} for k, v in transfer_status_counter.most_common()],
        "postTransferActions": [{"action": k, "count": v, "pct": round((v / operator_escalated) * 100, 1) if operator_escalated else 0} for k, v in post_transfer_counter.most_common()],
        "dealStages": [{"stage": k, "count": v} for k, v in deal_stages_counter.most_common(8)],
        "leadStages": [{"stage": k, "count": v} for k, v in lead_stages_counter.most_common(8)],
        "sources": [{"source": k, "count": v, "pct": round((v / total) * 100, 1)} for k, v in sources_counter.most_common(8)],
        "outcomes": [{"outcome": k, "count": v, "pct": round((v / total) * 100, 1)} for k, v in outcomes_counter.most_common()],
        "topSizes": [{"size": k, "count": v} for k, v in sizes_counter.most_common(12)],
        "topBrands": [{"brand": k, "count": v} for k, v in brands_counter.most_common(10)],
        "cities": [{"city": k, "count": v, "pct": round((v / total) * 100, 1)} for k, v in cities_counter.most_common(10)],
        "categories": [{"category": k, "count": v, "pct": round((v / total) * 100, 1)} for k, v in categories_counter.most_common()],
        "managers": manager_perf
    }

def main():
    print("=" * 70)
    print("AIKOS — Запуск полной конвертации диалогов в JSON и построения аналитики")
    print("=" * 70)

    os.makedirs(D_JSON_DIR, exist_ok=True)
    os.makedirs(D_JSON_MONTHLY_DIR, exist_ok=True)

    csv_files = sorted(glob.glob(os.path.join(DIALOG_DIR, "*.csv")))
    if not csv_files:
        print(f"[!] Ошибка: В {DIALOG_DIR} не найдено CSV файлов.")
        return

    print(f"[*] Найдено CSV файлов: {len(csv_files)}")

    dialogs_by_sid = {}

    for filepath in csv_files:
        filename = os.path.basename(filepath)
        print(f"  -> Обработка {filename}...")
        try:
            with open(filepath, mode='r', encoding='utf-8-sig', errors='replace') as f:
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    sid_key = next((k for k in row.keys() if 'SID' in k), None)
                    if not sid_key:
                        continue
                    sid = str(row.get(sid_key) or "").strip()
                    if not sid or sid in dialogs_by_sid:
                        continue

                    raw_text = str(row.get("Текст переписки") or "")
                    messages = parse_dialog_messages(raw_text)

                    operator_messages = [m for m in messages if m["role"] == "operator"]
                    bot_messages = [m for m in messages if m["role"] == "bot"]
                    client_messages = [m for m in messages if m["role"] == "client"]
                    operator_present = len(operator_messages) > 0

                    assigned_employee = str(row.get("Сотрудник") or "").strip()
                    operator_name = operator_messages[0]["sender"] if operator_present else (
                        assigned_employee if assigned_employee and assigned_employee != "AI ассистент" else "Только AI-бот"
                    )

                    tech_glitches = sum(1 for m in bot_messages if "техническая заминка" in m["text"].lower())

                    city = extract_city(raw_text, messages)
                    tire_sizes = extract_tire_sizes(raw_text)
                    brands = extract_brands(raw_text)
                    car_model = extract_car_model(raw_text)
                    category = extract_product_category(raw_text, tire_sizes, brands)
                    intents = extract_intents(raw_text)
                    source = extract_source(messages)
                    language = detect_language(messages)
                    outcome_label, outcome_type = extract_detailed_outcome(raw_text, messages, row, operator_present)

                    created_at = str(row.get("Дата создания") or "").strip()
                    closed_at = str(row.get("Дата последнего сообщения") or "").strip()
                    month_key = created_at[:7] if len(created_at) >= 7 else "Не указан"

                    # Анализ таймингов и передачи
                    bot_sec, op_wait_min, transfer_status, transfer_status_label = compute_dialog_timings(messages, operator_present)
                    post_transfer_action = determine_post_transfer(transfer_status, outcome_type, messages)

                    # CRM статусы
                    deal_stage = str(row.get("Сделка - Стадия") or "").strip() or "—"
                    lead_stage = str(row.get("Лид - Стадия") or "").strip() or "—"
                    deal_category = str(row.get("Сделка - Воронка (категория)") or "").strip() or "—"
                    deal_id = str(row.get("Сделка - ID") or "").strip()
                    lead_id = str(row.get("Лид - ID") or "").strip()

                    item = {
                        "sid": sid,
                        "client": str(row.get("Клиент") or "Клиент").strip(),
                        "channel": str(row.get("Канал") or "WhatsApp").strip(),
                        "lineName": str(row.get("Открытая линия") or "AI ассистент").strip(),
                        "createdAt": created_at,
                        "closedAt": closed_at,
                        "month": month_key,
                        "messagesCount": len(messages),
                        "clientMsgCount": len(client_messages),
                        "botMsgCount": len(bot_messages),
                        "operatorMsgCount": len(operator_messages),
                        "operatorPresent": operator_present,
                        "operator": operator_name,
                        "botResponseSec": bot_sec,
                        "operatorWaitMin": op_wait_min,
                        "transferStatus": transfer_status,
                        "transferStatusLabel": transfer_status_label,
                        "postTransferAction": post_transfer_action,
                        "dealStage": deal_stage,
                        "leadStage": lead_stage,
                        "dealCategory": deal_category,
                        "dealId": deal_id,
                        "leadId": lead_id,
                        "techGlitchesCount": tech_glitches,
                        "city": city,
                        "source": source,
                        "language": language,
                        "tireSizes": tire_sizes,
                        "primarySize": tire_sizes[0] if tire_sizes else "—",
                        "brands": brands,
                        "primaryBrand": brands[0] if brands else "—",
                        "carModel": car_model or "—",
                        "category": category,
                        "intents": intents,
                        "outcome": outcome_label,
                        "outcomeType": outcome_type,
                        "isSale": outcome_type == "sale",
                        "isService": outcome_type == "service",
                        "messages": messages
                    }
                    dialogs_by_sid[sid] = item
        except Exception as e:
            print(f"    [!] Ошибка чтения {filename}: {e}")

    all_dialogs = sorted(dialogs_by_sid.values(), key=lambda d: d.get("createdAt", ""), reverse=True)
    print(f"\n[+] Всего уникальных дедуплицированных диалогов: {len(all_dialogs)}")

    # 1. Группировка по месяцам
    monthly_dialogs = defaultdict(list)
    for d in all_dialogs:
        monthly_dialogs[d["month"]].append(d)

    # 2. Сохранение JSON по месяцам в dialog/d-json/monthly/
    monthly_summaries = {}
    print("\n[*] Экспорт помесячных JSON в dialog/d-json/monthly/:")
    for month, m_dialogs in sorted(monthly_dialogs.items()):
        month_filename = f"dialogs_{month.replace('-', '_')}.json"
        month_path = os.path.join(D_JSON_MONTHLY_DIR, month_filename)
        with open(month_path, "w", encoding="utf-8") as f:
            json.dump(m_dialogs, f, ensure_ascii=False, indent=2)
        print(f"  -> {month_filename}: {len(m_dialogs)} диалогов")
        monthly_summaries[month] = compute_metrics(m_dialogs)

    # 3. Сохранение общего сводного файла в dialog/d-json/
    overall_summary = compute_metrics(all_dialogs)
    timeline_days = Counter(d.get("createdAt", "")[:10] for d in all_dialogs if d.get("createdAt"))

    summary_payload = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totalDialogs": len(all_dialogs),
        "totalFilesProcessed": len(csv_files),
        "availableMonths": sorted(list(monthly_dialogs.keys())),
        "overall": overall_summary,
        "monthly": monthly_summaries,
        "dailyTimeline": [{"date": k, "count": v} for k, v in sorted(timeline_days.items())]
    }

    summary_path = os.path.join(D_JSON_DIR, "summary_monthly.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_payload, f, ensure_ascii=False, indent=2)
    print(f"[+] Сводная помесячная аналитика сохранена: {summary_path}")

    # Сохранение единого all_dialogs.json
    all_json_path = os.path.join(D_JSON_DIR, "all_dialogs.json")
    with open(all_json_path, "w", encoding="utf-8") as f:
        json.dump(all_dialogs, f, ensure_ascii=False, indent=2)
    print(f"[+] Все диалоги сохранены: {all_json_path}")

    # 4. Генерация интерактивного HTML-дашборда со ВСЕМИ диалогами и пагинацией
    print("\n[*] Генерация интерактивного дашборда со всеми диалогами и пагинацией...")
    generate_full_dashboard(summary_payload, all_dialogs)
    print(f"[+] Готово! Дашборд создан: {DASHBOARD_HTML_PATH}")
    print("=" * 70)

def generate_full_dashboard(summary_data, all_dialogs):
    """Генерирует HTML дашборд со всеми диалогами, пагинацией, фильтрами и таймингами."""
    embedded_summary = json.dumps(summary_data, ensure_ascii=False)
    # Компактный JSON без переносов строк для быстрой загрузки браузером
    embedded_dialogs = json.dumps(all_dialogs, ensure_ascii=False, separators=(',', ':'))

    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AIKOS Intelligence — Полная аналитика диалогов AI-ассистента</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        :root {{
            --bg-base: #0B0F19;
            --bg-card: #121826;
            --bg-card-hover: #1A2234;
            --accent-primary: #3B82F6;
            --accent-cyan: #06B6D4;
            --accent-purple: #8B5CF6;
            --accent-success: #10B981;
            --accent-warning: #F59E0B;
            --accent-danger: #EF4444;
            --text-main: #F3F4F6;
            --text-muted: #9CA3AF;
            --border-color: #1F2937;
            --border-hover: #374151;
            --card-radius: 14px;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: var(--bg-base);
            color: var(--text-main);
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
            min-height: 100vh;
            padding: 24px;
        }}

        .header-section {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
            flex-wrap: wrap;
            gap: 16px;
        }}
        .brand-title {{
            font-size: 26px;
            font-weight: 800;
            background: linear-gradient(135deg, #60A5FA, #A78BFA, #34D399);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .brand-subtitle {{
            font-size: 13px;
            color: var(--text-muted);
            margin-top: 4px;
        }}

        /* Месячный переключатель */
        .month-tabs {{
            display: flex;
            gap: 8px;
            background: rgba(255,255,255,0.03);
            padding: 6px;
            border-radius: 12px;
            border: 1px solid var(--border-color);
            flex-wrap: wrap;
        }}
        .month-tab-btn {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .month-tab-btn:hover {{
            color: #fff;
            background: rgba(255,255,255,0.06);
        }}
        .month-tab-btn.active {{
            background: var(--accent-primary);
            color: #fff;
            box-shadow: 0 0 12px rgba(59, 130, 246, 0.4);
        }}

        /* Сетка KPI карточек */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .kpi-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--card-radius);
            padding: 18px;
            position: relative;
            overflow: hidden;
            transition: transform 0.2s, border-color 0.2s;
        }}
        .kpi-card:hover {{
            transform: translateY(-2px);
            border-color: var(--border-hover);
        }}
        .kpi-label {{
            font-size: 11px;
            text-transform: uppercase;
            font-weight: 700;
            color: var(--text-muted);
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}
        .kpi-value {{
            font-size: 26px;
            font-weight: 800;
            line-height: 1;
            margin-bottom: 6px;
        }}
        .kpi-subtext {{
            font-size: 12px;
            color: var(--text-muted);
        }}

        /* Графики */
        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(12, 1fr);
            gap: 20px;
            margin-bottom: 24px;
        }}
        .chart-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--card-radius);
            padding: 20px;
            display: flex;
            flex-direction: column;
        }}
        .col-12 {{ grid-column: span 12; }}
        .col-8 {{ grid-column: span 8; }}
        .col-6 {{ grid-column: span 6; }}
        .col-4 {{ grid-column: span 4; }}

        @media (max-width: 1024px) {{
            .col-8, .col-6, .col-4 {{ grid-column: span 12; }}
        }}

        .chart-title {{
            font-size: 14px;
            font-weight: 700;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .chart-container {{
            position: relative;
            flex: 1;
            min-height: 250px;
        }}

        /* Панель фильтров таблицы */
        .filters-panel {{
            display: flex;
            gap: 12px;
            margin-bottom: 16px;
            flex-wrap: wrap;
            align-items: center;
        }}
        .search-input {{
            background: rgba(255,255,255,0.05);
            border: 1px solid var(--border-color);
            color: #fff;
            padding: 9px 14px;
            border-radius: 8px;
            font-size: 13px;
            min-width: 280px;
            flex: 1;
        }}
        .search-input:focus {{
            outline: none;
            border-color: var(--accent-primary);
        }}
        .select-filter {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: #E5E7EB;
            padding: 9px 12px;
            border-radius: 8px;
            font-size: 13px;
            cursor: pointer;
        }}
        .select-filter:focus {{
            outline: none;
            border-color: var(--accent-primary);
        }}
        .btn-reset {{
            background: rgba(255,255,255,0.05);
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            padding: 9px 14px;
            border-radius: 8px;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .btn-reset:hover {{
            background: rgba(255,255,255,0.1);
            color: #fff;
        }}

        /* Таблица диалогов */
        .table-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--card-radius);
            padding: 20px;
            overflow: hidden;
        }}
        .table-wrapper {{
            overflow-x: auto;
        }}
        .custom-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            white-space: nowrap;
        }}
        .custom-table th {{
            text-align: left;
            padding: 12px 14px;
            background: rgba(255,255,255,0.02);
            color: var(--text-muted);
            font-weight: 600;
            border-bottom: 1px solid var(--border-color);
        }}
        .custom-table td {{
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255,255,255,0.04);
            color: #E5E7EB;
        }}
        .custom-table tr:hover td {{
            background: rgba(255,255,255,0.02);
            cursor: pointer;
        }}

        /* Бейджи */
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
        }}
        .badge-sale {{ background: rgba(16, 185, 129, 0.15); color: #34D399; }}
        .badge-service {{ background: rgba(59, 130, 246, 0.15); color: #60A5FA; }}
        .badge-bot {{ background: rgba(139, 92, 246, 0.15); color: #A78BFA; }}
        .badge-lost {{ background: rgba(239, 68, 68, 0.15); color: #F87171; }}
        .badge-glitch {{ background: rgba(245, 158, 11, 0.15); color: #FBBF24; }}
        .badge-fast {{ background: rgba(16, 185, 129, 0.15); color: #10B981; }}
        .badge-slow {{ background: rgba(245, 158, 11, 0.15); color: #F59E0B; }}
        .badge-danger {{ background: rgba(239, 68, 68, 0.15); color: #EF4444; }}

        /* Пагинация */
        .pagination-container {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid var(--border-color);
            flex-wrap: wrap;
            gap: 12px;
        }}
        .pagination-info {{
            font-size: 13px;
            color: var(--text-muted);
        }}
        .pagination-controls {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .page-btn {{
            background: rgba(255,255,255,0.04);
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .page-btn:hover:not(:disabled) {{
            background: rgba(255,255,255,0.1);
        }}
        .page-btn.active {{
            background: var(--accent-primary);
            color: #fff;
            border-color: var(--accent-primary);
        }}
        .page-btn:disabled {{
            opacity: 0.4;
            cursor: not-allowed;
        }}

        /* Модальное окно переписки */
        .modal-overlay {{
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.75);
            backdrop-filter: blur(4px);
            z-index: 1000;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .modal-dialog {{
            background: var(--bg-card);
            border: 1px solid var(--border-hover);
            border-radius: var(--card-radius);
            width: 100%;
            max-width: 820px;
            max-height: 85vh;
            display: flex;
            flex-direction: column;
            box-shadow: 0 20px 40px rgba(0,0,0,0.5);
        }}
        .modal-header {{
            padding: 16px 20px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .modal-body {{
            padding: 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .msg-bubble {{
            padding: 12px 16px;
            border-radius: 12px;
            max-width: 82%;
            line-height: 1.45;
            font-size: 13px;
        }}
        .msg-client {{
            align-self: flex-start;
            background: #1E293B;
            border: 1px solid #334155;
        }}
        .msg-bot {{
            align-self: flex-end;
            background: rgba(59, 130, 246, 0.15);
            border: 1px solid rgba(59, 130, 246, 0.3);
        }}
        .msg-operator {{
            align-self: flex-end;
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }}
        .msg-meta {{
            font-size: 11px;
            color: var(--text-muted);
            margin-bottom: 4px;
        }}
    </style>
</head>
<body>

    <div class="header-section">
        <div>
            <h1 class="brand-title">AIKOS Intelligence — Аналитика диалогов ИИ-ассистента</h1>
            <div class="brand-subtitle">Bitrix24 Openlines WhatsApp | Анализ времени ответа, передачи операторам, статусов CRM и воронки</div>
        </div>
        <div class="month-tabs" id="monthTabsContainer"></div>
    </div>

    <!-- Карточки ключевых метрик (KPI) -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-label">Всего диалогов</div>
            <div class="kpi-value" id="kpiTotalDialogs">0</div>
            <div class="kpi-subtext" id="kpiTotalMessages">0 сообщений в базе</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Время ответа ИИ-бота</div>
            <div class="kpi-value" id="kpiBotTime" style="color: #60A5FA;">⚡ 0 с</div>
            <div class="kpi-subtext">Среднее время первого ответа</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Ожидание оператора</div>
            <div class="kpi-value" id="kpiOpWait" style="color: var(--accent-warning);">⏳ 0 мин</div>
            <div class="kpi-subtext">Среднее время после передачи</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Оператор НЕ ответил</div>
            <div class="kpi-value" id="kpiOpNoReply" style="color: var(--accent-danger);">0</div>
            <div class="kpi-subtext" id="kpiOpNoReplyPct">0% от переданных диалогов</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Автономность AI-бота</div>
            <div class="kpi-value" id="kpiBotAutonomy" style="color: var(--accent-purple);">0%</div>
            <div class="kpi-subtext" id="kpiBotSubtext">0 закрыто без оператора</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Общая конверсия</div>
            <div class="kpi-value" id="kpiConversion" style="color: var(--accent-success);">0%</div>
            <div class="kpi-subtext" id="kpiConversionSubtext">Продажи + Сервис</div>
        </div>
    </div>

    <!-- Интерактивные графики -->
    <div class="charts-grid">
        <div class="chart-card col-8">
            <div class="chart-title">📈 Помесячная динамика нагрузки и конверсий</div>
            <div class="chart-container"><canvas id="monthlyTrendChart"></canvas></div>
        </div>
        <div class="chart-card col-4">
            <div class="chart-title">⏱️ Скорость первого ответа оператора</div>
            <div class="chart-container"><canvas id="transferSpeedChart"></canvas></div>
        </div>

        <div class="chart-card col-6">
            <div class="chart-title">🔄 Что происходит после передачи оператору</div>
            <div class="chart-container"><canvas id="postTransferChart"></canvas></div>
        </div>
        <div class="chart-card col-6">
            <div class="chart-title">🎯 Исходы всех обращений</div>
            <div class="chart-container"><canvas id="outcomesChart"></canvas></div>
        </div>

        <div class="chart-card col-6">
            <div class="chart-title">🚗 Топ запрашиваемых типоразмеров (без мусора)</div>
            <div class="chart-container"><canvas id="sizesChart"></canvas></div>
        </div>
        <div class="chart-card col-6">
            <div class="chart-title">🏷️ Спрос по брендам шин и дисков</div>
            <div class="chart-container"><canvas id="brandsChart"></canvas></div>
        </div>

        <div class="chart-card col-6">
            <div class="chart-title">💼 Стадии сделок в CRM Bitrix24</div>
            <div class="chart-container"><canvas id="dealStagesChart"></canvas></div>
        </div>
        <div class="chart-card col-6">
            <div class="chart-title">👨‍💼 Скорость ответа и результативность менеджеров</div>
            <div class="chart-container"><canvas id="managersChart"></canvas></div>
        </div>
    </div>

    <!-- Таблица со ВСЕМИ диалогами и пагинацией -->
    <div class="table-card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; flex-wrap:wrap; gap:10px;">
            <div class="chart-title" style="margin-bottom:0;">💬 Журнал обращений (Кликните по строке для просмотра всей переписки)</div>
            <div style="font-size:13px; color:var(--text-muted);" id="tableCountLabel">Загрузка диалогов...</div>
        </div>

        <!-- Фильтры над таблицей -->
        <div class="filters-panel">
            <input type="text" id="tableSearchInput" class="search-input" placeholder="🔍 Поиск по SID, клиенту, городу, типоразмеру, тексту...">
            
            <select id="filterTransfer" class="select-filter" onchange="applyFilters()">
                <option value="all">⚡ Все статусы передачи</option>
                <option value="bot_only">🤖 Только AI-бот</option>
                <option value="op_fast">⚡ Отвечено быстро (≤5 мин)</option>
                <option value="op_normal">⏱️ Отвечено штатно (5-15 мин)</option>
                <option value="op_slow">⏳ Отвечено с задержкой (15-60 мин)</option>
                <option value="op_critical">🚨 Критическая задержка (>1 ч)</option>
                <option value="op_no_reply">❌ Оператор НЕ ответил</option>
            </select>

            <select id="filterOutcome" class="select-filter" onchange="applyFilters()">
                <option value="all">🎯 Все исходы</option>
                <option value="sale">🏆 Продажи / Заказы</option>
                <option value="service">🔧 Записи на сервис</option>
                <option value="lost">❌ Отказы / Ушли</option>
                <option value="consultation">💬 Консультации</option>
            </select>

            <select id="filterManager" class="select-filter" onchange="applyFilters()">
                <option value="all">👨‍💼 Все менеджеры</option>
            </select>

            <select id="filterCity" class="select-filter" onchange="applyFilters()">
                <option value="all">📍 Все города</option>
            </select>

            <button class="btn-reset" onclick="resetFilters()">Сбросить</button>
        </div>

        <!-- Таблица -->
        <div class="table-wrapper">
            <table class="custom-table">
                <thead>
                    <tr>
                        <th>SID / Время</th>
                        <th>Клиент</th>
                        <th>Источник</th>
                        <th>Город</th>
                        <th>Типоразмер / Авто</th>
                        <th>Кто вел</th>
                        <th>Время ответа</th>
                        <th>Статус передачи</th>
                        <th>Статус CRM</th>
                        <th>Что дальше / Исход</th>
                        <th>Сообщений</th>
                    </tr>
                </thead>
                <tbody id="dialogsTableBody"></tbody>
            </table>
        </div>

        <!-- Пагинация -->
        <div class="pagination-container">
            <div class="pagination-info" id="paginationInfo">Показано 0–0 из 0</div>
            <div style="display:flex; align-items:center; gap:12px;">
                <label style="font-size:12px; color:var(--text-muted);">
                    На странице:
                    <select id="pageSizeSelect" class="select-filter" style="padding:4px 8px; font-size:12px;" onchange="changePageSize()">
                        <option value="25">25</option>
                        <option value="50" selected>50</option>
                        <option value="100">100</option>
                        <option value="200">200</option>
                    </select>
                </label>
                <div class="pagination-controls" id="paginationControls"></div>
            </div>
        </div>
    </div>

    <!-- Модальное окно переписки -->
    <div class="modal-overlay" id="dialogModal" onclick="closeModal(event)">
        <div class="modal-dialog" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div>
                    <h3 id="modalTitle" style="font-size:16px; font-weight:700;">Диалог #</h3>
                    <div id="modalMeta" style="font-size:12px; color:var(--text-muted); margin-top:2px;"></div>
                </div>
                <button onclick="closeModal()" style="background:transparent; border:none; color:var(--text-muted); font-size:20px; cursor:pointer;">✕</button>
            </div>
            <div class="modal-body" id="modalChatBody"></div>
        </div>
    </div>

    <script>
        const summaryData = {embedded_summary};
        const allDialogs = {embedded_dialogs};

        let currentMonth = "all";
        let filteredDialogs = [];
        let currentPage = 1;
        let pageSize = 50;
        let charts = {{}};

        function initDashboard() {{
            populateFilterOptions();
            renderMonthTabs();
            applyPeriod("all");
            setupSearchDebounce();
        }}

        function populateFilterOptions() {{
            const managerSelect = document.getElementById("filterManager");
            const citySelect = document.getElementById("filterCity");

            const managers = new Set();
            const cities = new Set();

            allDialogs.forEach(d => {{
                if (d.operator && d.operator !== "Только AI-бот") managers.add(d.operator);
                if (d.city && d.city !== "Не определен") cities.add(d.city);
            }});

            Array.from(managers).sort().forEach(m => {{
                const opt = document.createElement("option");
                opt.value = m;
                opt.innerText = m;
                managerSelect.appendChild(opt);
            }});

            Array.from(cities).sort().forEach(c => {{
                const opt = document.createElement("option");
                opt.value = c;
                opt.innerText = c;
                citySelect.appendChild(opt);
            }});
        }}

        function renderMonthTabs() {{
            const container = document.getElementById("monthTabsContainer");
            const months = summaryData.availableMonths || [];
            
            let html = `<button class="month-tab-btn active" onclick="applyPeriod('all')">📅 Все периоды</button>`;
            const monthNames = {{
                "2026-04": "Апрель", "2026-05": "Май", "2026-06": "Июнь",
                "2026-07": "Июль", "2026-08": "Август", "2026-09": "Сентябрь", "2026-10": "Октябрь"
            }};

            months.forEach(m => {{
                const label = monthNames[m] || m;
                html += `<button class="month-tab-btn" onclick="applyPeriod('${{m}}')">${{label}}</button>`;
            }});
            container.innerHTML = html;
        }}

        function applyPeriod(monthKey) {{
            currentMonth = monthKey;
            
            document.querySelectorAll(".month-tab-btn").forEach(btn => {{
                if ((monthKey === "all" && btn.innerText.includes("Все")) || btn.onclick.toString().includes(monthKey)) {{
                    btn.classList.add("active");
                }} else {{
                    btn.classList.remove("active");
                }}
            }});

            const data = (monthKey === "all") ? summaryData.overall : (summaryData.monthly[monthKey] || summaryData.overall);

            // Обновление KPI
            document.getElementById("kpiTotalDialogs").innerText = (data.totalDialogs || 0).toLocaleString();
            document.getElementById("kpiTotalMessages").innerText = `${{(data.totalMessages || 0).toLocaleString()}} сообщений`;
            document.getElementById("kpiBotTime").innerText = `⚡ ${{data.avgBotTimeSec || 0}} с`;
            document.getElementById("kpiOpWait").innerText = `⏳ ${{data.avgOperatorWaitMin || 0}} мин`;
            document.getElementById("kpiOpNoReply").innerText = (data.opNoReplyCount || 0).toLocaleString();
            document.getElementById("kpiOpNoReplyPct").innerText = `${{data.opNoReplyPct || 0}}% без ответа оператора`;
            document.getElementById("kpiBotAutonomy").innerText = `${{data.botOnlyPct || 0}}%`;
            document.getElementById("kpiBotSubtext").innerText = `${{data.botOnlyCount || 0}} закрыто без оператора`;
            document.getElementById("kpiConversion").innerText = `${{data.conversionRate || 0}}%`;
            document.getElementById("kpiConversionSubtext").innerText = `${{data.totalConversions || 0}} успешных исходов`;

            renderCharts(data);
            applyFilters();
        }}

        function applyFilters() {{
            const search = document.getElementById("tableSearchInput").value.toLowerCase().trim();
            const transfer = document.getElementById("filterTransfer").value;
            const outcome = document.getElementById("filterOutcome").value;
            const manager = document.getElementById("filterManager").value;
            const city = document.getElementById("filterCity").value;

            filteredDialogs = allDialogs.filter(d => {{
                if (currentMonth !== "all" && d.month !== currentMonth) return false;
                if (transfer !== "all" && d.transferStatus !== transfer) return false;
                if (outcome !== "all" && d.outcomeType !== outcome) return false;
                if (manager !== "all" && d.operator !== manager) return false;
                if (city !== "all" && d.city !== city) return false;

                if (search) {{
                    const matchText = (d.sid + " " + d.client + " " + d.city + " " + d.primarySize + " " + d.primaryBrand + " " + d.operator + " " + (d.dealStage || "") + " " + (d.leadStage || "")).toLowerCase();
                    if (!matchText.includes(search)) return false;
                }}
                return true;
            }});

            currentPage = 1;
            renderTablePage();
        }}

        function resetFilters() {{
            document.getElementById("tableSearchInput").value = "";
            document.getElementById("filterTransfer").value = "all";
            document.getElementById("filterOutcome").value = "all";
            document.getElementById("filterManager").value = "all";
            document.getElementById("filterCity").value = "all";
            applyFilters();
        }}

        function setupSearchDebounce() {{
            let timeout = null;
            document.getElementById("tableSearchInput").addEventListener("input", () => {{
                clearTimeout(timeout);
                timeout = setTimeout(applyFilters, 250);
            }});
        }}

        function changePageSize() {{
            pageSize = parseInt(document.getElementById("pageSizeSelect").value, 10);
            currentPage = 1;
            renderTablePage();
        }}

        function renderTablePage() {{
            const total = filteredDialogs.length;
            const totalPages = Math.max(1, Math.ceil(total / pageSize));
            if (currentPage > totalPages) currentPage = totalPages;

            const startIdx = (currentPage - 1) * pageSize;
            const endIdx = Math.min(startIdx + pageSize, total);
            const pageData = filteredDialogs.slice(startIdx, endIdx);

            document.getElementById("tableCountLabel").innerText = `Найдено ${{total.toLocaleString()}} диалогов`;
            document.getElementById("paginationInfo").innerText = total > 0 
                ? `Показано ${{startIdx + 1}}–${{endIdx}} из ${{total.toLocaleString()}} (страница ${{currentPage}} из ${{totalPages}})`
                : `Нет диалогов`;

            const tbody = document.getElementById("dialogsTableBody");
            let html = "";

            pageData.forEach(d => {{
                let outcomeBadge = "badge-bot";
                if (d.outcomeType === "sale") outcomeBadge = "badge-sale";
                else if (d.outcomeType === "service") outcomeBadge = "badge-service";
                else if (d.outcomeType === "lost") outcomeBadge = "badge-lost";

                let transferBadge = "badge-bot";
                if (d.transferStatus === "op_fast") transferBadge = "badge-fast";
                else if (d.transferStatus === "op_normal") transferBadge = "badge-service";
                else if (d.transferStatus === "op_slow") transferBadge = "badge-slow";
                else if (d.transferStatus === "op_critical" || d.transferStatus === "op_no_reply") transferBadge = "badge-danger";

                const botWaitText = d.botResponseSec !== null ? `⚡ ${{d.botResponseSec}}с` : `—`;
                const opWaitText = d.operatorWaitMin !== null ? `⏳ ${{d.operatorWaitMin}}м` : (d.operatorPresent ? `❌ Нет` : `—`);

                const crmText = (d.dealStage && d.dealStage !== "—") ? d.dealStage : ((d.leadStage && d.leadStage !== "—") ? `Лид: ${{d.leadStage}}` : `—`);

                html += `<tr onclick="openModal('${{d.sid}}')">
                    <td><strong>#${{d.sid}}</strong><br><span style="font-size:11px; color:var(--text-muted);">${{d.createdAt}}</span></td>
                    <td><strong>${{d.client}}</strong></td>
                    <td><span style="font-size:12px; color:var(--text-muted);">${{d.source}}</span></td>
                    <td>${{d.city}}</td>
                    <td><span style="font-family:'JetBrains Mono', monospace; color:#93C5FD;">${{d.primarySize}}</span><br><span style="font-size:11px; color:var(--text-muted);">${{d.carModel !== '—' ? d.carModel : d.primaryBrand}}</span></td>
                    <td>${{d.operatorPresent ? '👨‍💼 ' + d.operator : '🤖 AI-бот'}}</td>
                    <td><span style="font-size:11px;">ИИ: ${{botWaitText}}<br>Оп: ${{opWaitText}}</span></td>
                    <td><span class="badge ${{transferBadge}}">${{d.transferStatusLabel}}</span></td>
                    <td><span style="font-size:11px; color:#A78BFA;">${{crmText}}</span></td>
                    <td><span class="badge ${{outcomeBadge}}">${{d.postTransferAction}}</span></td>
                    <td>${{d.messagesCount}}</td>
                </tr>`;
            }});

            tbody.innerHTML = html || `<tr><td colspan="11" style="text-align:center; padding:30px; color:var(--text-muted);">Диалоги не найдены по текущим фильтрам</td></tr>`;

            renderPaginationControls(totalPages);
        }}

        function renderPaginationControls(totalPages) {{
            const container = document.getElementById("paginationControls");
            let html = "";

            html += `<button class="page-btn" onclick="goToPage(1)" ${{currentPage === 1 ? 'disabled' : ''}}>⏮</button>`;
            html += `<button class="page-btn" onclick="goToPage(${{currentPage - 1}})" ${{currentPage === 1 ? 'disabled' : ''}}>◀</button>`;

            const maxVisible = 5;
            let start = Math.max(1, currentPage - 2);
            let end = Math.min(totalPages, start + maxVisible - 1);
            if (end - start < maxVisible - 1) {{
                start = Math.max(1, end - maxVisible + 1);
            }}

            for (let p = start; p <= end; p++) {{
                html += `<button class="page-btn ${{p === currentPage ? 'active' : ''}}" onclick="goToPage(${{p}})">${{p}}</button>`;
            }}

            html += `<button class="page-btn" onclick="goToPage(${{currentPage + 1}})" ${{currentPage === totalPages ? 'disabled' : ''}}>▶</button>`;
            html += `<button class="page-btn" onclick="goToPage(${{totalPages}})" ${{currentPage === totalPages ? 'disabled' : ''}}>⏭</button>`;

            container.innerHTML = html;
        }}

        function goToPage(p) {{
            currentPage = p;
            renderTablePage();
            document.querySelector(".table-card").scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
        }}

        function renderCharts(data) {{
            const months = summaryData.availableMonths || [];
            const trendLabels = months.map(m => m);
            const trendDialogs = months.map(m => summaryData.monthly[m]?.totalDialogs || 0);
            const trendSales = months.map(m => summaryData.monthly[m]?.salesCount || 0);
            const trendService = months.map(m => summaryData.monthly[m]?.serviceCount || 0);

            // 1. Помесячный тренд
            createOrUpdateChart("monthlyTrendChart", {{
                type: 'bar',
                data: {{
                    labels: trendLabels,
                    datasets: [
                        {{ label: 'Всего диалогов', data: trendDialogs, backgroundColor: 'rgba(59, 130, 246, 0.4)', borderColor: '#3B82F6', borderWidth: 1 }},
                        {{ label: 'Продажи', data: trendSales, backgroundColor: '#10B981' }},
                        {{ label: 'Записи на сервис', data: trendService, backgroundColor: '#06B6D4' }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, scales: {{ y: {{ beginAtZero: true }} }} }}
            }});

            // 2. Скорость ответа оператора
            const transferStats = data.transferStatuses || [];
            createOrUpdateChart("transferSpeedChart", {{
                type: 'doughnut',
                data: {{
                    labels: transferStats.map(t => t.label),
                    datasets: [{{
                        data: transferStats.map(t => t.count),
                        backgroundColor: ['#8B5CF6', '#10B981', '#3B82F6', '#F59E0B', '#EF4444']
                    }}]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'right' }} }} }}
            }});

            // 3. Что происходит после передачи
            const postTransfer = data.postTransferActions || [];
            createOrUpdateChart("postTransferChart", {{
                type: 'bar',
                data: {{
                    labels: postTransfer.map(p => p.action),
                    datasets: [{{
                        label: 'Количество диалогов',
                        data: postTransfer.map(p => p.count),
                        backgroundColor: ['#10B981', '#06B6D4', '#3B82F6', '#F59E0B', '#EF4444', '#64748B']
                    }}]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }} }}
            }});

            // 4. Исходы обращений
            createOrUpdateChart("outcomesChart", {{
                type: 'doughnut',
                data: {{
                    labels: (data.outcomes || []).map(o => o.outcome),
                    datasets: [{{
                        data: (data.outcomes || []).map(o => o.count),
                        backgroundColor: ['#10B981', '#3B82F6', '#8B5CF6', '#F59E0B', '#EF4444', '#64748B']
                    }}]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'right' }} }} }}
            }});

            // 5. Типоразмеры
            createOrUpdateChart("sizesChart", {{
                type: 'bar',
                data: {{
                    labels: (data.topSizes || []).map(s => s.size),
                    datasets: [{{
                        label: 'Запросов',
                        data: (data.topSizes || []).map(s => s.count),
                        backgroundColor: '#60A5FA',
                        borderRadius: 4
                    }}]
                }},
                options: {{ indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }} }}
            }});

            // 6. Бренды
            createOrUpdateChart("brandsChart", {{
                type: 'bar',
                data: {{
                    labels: (data.topBrands || []).map(b => b.brand),
                    datasets: [{{
                        label: 'Упоминаний',
                        data: (data.topBrands || []).map(b => b.count),
                        backgroundColor: '#34D399',
                        borderRadius: 4
                    }}]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }} }}
            }});

            // 7. Стадии сделок CRM
            createOrUpdateChart("dealStagesChart", {{
                type: 'bar',
                data: {{
                    labels: (data.dealStages || []).map(s => s.stage),
                    datasets: [{{
                        label: 'Сделок на стадии',
                        data: (data.dealStages || []).map(s => s.count),
                        backgroundColor: '#A78BFA',
                        borderRadius: 4
                    }}]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }} }}
            }});

            // 8. Менеджеры: результативность и время ожидания
            createOrUpdateChart("managersChart", {{
                type: 'bar',
                data: {{
                    labels: (data.managers || []).map(m => m.name),
                    datasets: [
                        {{ label: 'Продажи', data: (data.managers || []).map(m => m.sales), backgroundColor: '#10B981' }},
                        {{ label: 'Сервис', data: (data.managers || []).map(m => m.service), backgroundColor: '#06B6D4' }},
                        {{ label: 'Без ответа (потери)', data: (data.managers || []).map(m => m.noReply), backgroundColor: '#EF4444' }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, scales: {{ x: {{ stacked: true }}, y: {{ stacked: true }} }} }}
            }});
        }}

        function createOrUpdateChart(canvasId, config) {{
            if (charts[canvasId]) charts[canvasId].destroy();
            const ctx = document.getElementById(canvasId).getContext('2d');
            charts[canvasId] = new Chart(ctx, config);
        }}

        function openModal(sid) {{
            const d = allDialogs.find(item => item.sid === sid);
            if (!d) return;

            document.getElementById("modalTitle").innerText = `Диалог #${{d.sid}} — ${{d.client}} (${{d.city}})`;
            
            const botWait = d.botResponseSec !== null ? `⚡ Ответ бота: ${{d.botResponseSec}} сек` : `Бот: —`;
            const opWait = d.operatorWaitMin !== null ? `⏳ Ответ оператора: ${{d.operatorWaitMin}} мин` : (d.operatorPresent ? `❌ Оператор не ответил` : `Только бот`);
            const crmInfo = (d.dealStage && d.dealStage !== "—") ? `Сделка: ${{d.dealStage}}` : `Лид: ${{d.leadStage || '—'}}`;

            document.getElementById("modalMeta").innerText = `${{d.createdAt}} | ${{d.source}} | ${{botWait}} | ${{opWait}} | ${{crmInfo}} | Исход: ${{d.postTransferAction}}`;

            const chatBody = document.getElementById("modalChatBody");
            let chatHtml = "";
            (d.messages || []).forEach(m => {{
                let bubbleClass = "msg-client";
                let senderLabel = `👤 ${{m.sender}}`;
                if (m.role === "bot") {{
                    bubbleClass = "msg-bot";
                    senderLabel = `🤖 ${{m.sender}} (AI-бот)`;
                }} else if (m.role === "operator") {{
                    bubbleClass = "msg-operator";
                    senderLabel = `👨‍💼 ${{m.sender}} (Оператор)`;
                }}

                chatHtml += `<div class="msg-bubble ${{bubbleClass}}">
                    <div class="msg-meta">${{senderLabel}} • ${{m.time}}</div>
                    <div>${{m.text.replace(/\\n/g, '<br>')}}</div>
                </div>`;
            }});
            chatBody.innerHTML = chatHtml || `<div style="color:var(--text-muted); text-align:center;">Текст переписки отсутствует</div>`;
            document.getElementById("dialogModal").style.display = "flex";
        }}

        function closeModal(e) {{
            if (!e || e.target.id === "dialogModal" || e.target.tagName === "BUTTON") {{
                document.getElementById("dialogModal").style.display = "none";
            }}
        }}

        window.addEventListener("DOMContentLoaded", initDashboard);
    </script>
</body>
</html>
"""
    with open(DASHBOARD_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)

if __name__ == "__main__":
    main()
