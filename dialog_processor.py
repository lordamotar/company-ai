#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIKOS CRM — Модуль аналитики текстовых диалогов Bitrix24 (Открытые линии / WhatsApp).
Автономный анализатор выгрузок без обязательного подключения к базе данных.
Поддерживает извлечение источников обращений, анализ исходов (продажи, запись на сервис),
дедупликацию по SID, агрегацию метрик и генерацию автономного интерактивного дашборда.
"""

import os
import sys
import re
import csv
import json
import glob
import time
import argparse
import logging
from datetime import datetime
from collections import Counter, defaultdict

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIALOG_DIR = os.path.join(BASE_DIR, "dialog")
OUTPUT_JSON_PATH = os.path.join(DIALOG_DIR, "dialogs_data.json")
DASHBOARD_HTML_PATH = os.path.join(DIALOG_DIR, "dashboard.html")

# Справочники для извлечения сущностей
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

def parse_dialog_messages(raw_text):
    """
    Разбирает многострочный лог диалога на структурированные сообщения.
    Формат лога: [YYYY-MM-DD HH:MM:SS] Имя (роль): текст...
    """
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
        
        msg_text = raw_text[start_idx:end_idx].strip()
        clean_text = re.sub(r'\[URL\](.*?)\[/URL\]', r'\1', msg_text)

        # Классификация роли
        if "new" in sender.lower() or "бот" in role_raw or "ассистент" in sender.lower():
            role = "bot"
        elif "оператор" in role_raw or "сотрудник" in role_raw or any(op in sender.lower() for op in ["усманова", "жумадилов", "жаназаров", "нуртазанов"]):
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

def extract_city(text, messages):
    """Определяет город клиента из переписки."""
    # 1. Поиск по репликам клиента (наивысший приоритет)
    client_text = " ".join([m["text"] for m in messages if m["role"] == "client"])
    for city in CITIES:
        pattern = rf'\b{city}\b'
        if re.search(pattern, client_text, re.I):
            return "Астана" if city == "Нур-Султан" else city

    # 2. Поиск по репликам бота/оператора
    for city in CITIES:
        pattern = rf'(?:в|по|город|г\.)\s+{city}\b|\b{city}\b'
        if re.search(pattern, text, re.I):
            return "Астана" if city == "Нур-Султан" else city

    return "Не определен"

def extract_tire_sizes(text):
    """Находит типоразмеры шин и дисков."""
    sizes = set()
    matches = re.findall(r'\b(\d{3}[\s/xX\-]\d{2}[\s/xXRr]*\d{2})\b', text)
    for m in matches:
        clean = re.sub(r'[\s\-xX]', '/', m)
        clean = re.sub(r'/+R?', '/', clean, flags=re.I)
        parts = clean.split('/')
        if len(parts) >= 3 and len(parts[0]) == 3 and len(parts[1]) == 2:
            std_size = f"{parts[0]}/{parts[1]} R{parts[2]}"
            sizes.add(std_size)

    # Диски одиночные: Р 17, R18, R16
    if not sizes:
        disk_matches = re.findall(r'\b[RР]\s*(\d{2})\b', text, re.I)
        for d in disk_matches:
            if int(d) in range(13, 23):
                sizes.add(f"Диски R{d}")

    return list(sizes)

def extract_brands(text):
    """Находит упоминания брендов шин и дисков."""
    found = set()
    for brand in BRANDS:
        if re.search(rf'\b{brand}\b', text, re.I):
            found.add(brand)
    return list(found)

def extract_car_model(text):
    """Определяет марку/модель авто."""
    for car in CAR_MODELS:
        if re.search(rf'\b{car}\b', text, re.I):
            return car
    return None

def extract_product_category(text, tire_sizes, brands):
    """Определяет основную категорию товара/услуги."""
    text_lower = text.lower()
    if "диск" in text_lower or "skad" in text_lower or "trebl" in text_lower or "литые" in text_lower:
        return "Диски"
    if "шип" in text_lower or "зимн" in text_lower or "липучк" in text_lower or "ice" in text_lower or "winter" in text_lower:
        return "Зимние шины"
    if "летн" in text_lower or "summer" in text_lower or "лето" in text_lower:
        return "Летние шины"
    if "хранен" in text_lower or "монтаж" in text_lower or "балансировк" in text_lower or "запис" in text_lower:
        return "Сервис / Услуги"
    if tire_sizes or brands:
        return "Шины (Общее)"
    return "Общая консультация"

def extract_intents(text):
    """Определяет ключевые вопросы и намерения клиента."""
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

def extract_source(text, messages, row):
    """
    Определяет реальный источник обращения исключительно по репликам самого клиента
    и системным преамбулам приложений (например, 'Пишу из приложения 2ГИС', 'Обращение из Яндекс Карт').
    Исключает ложное срабатывание по приветственным фразам виртуального AI-бота.
    """
    client_messages = [m["text"] for m in messages if m["role"] == "client"]
    client_text = " ".join(client_messages).lower() if client_messages else ""
    
    if not client_text:
        return "WhatsApp (Прямой)"
    
    # 1. Системные шаблоны интеграций (2ГИС, Яндекс)
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
    if re.search(r'вывеск|мимо\s+ехал|увидел\s+магазин|баннер\s+на\s+дорог', client_text):
        return "Наружная реклама"
    if re.search(r'тик\s*ток|tiktok', client_text):
        return "TikTok"
    if re.search(r'нашел\s+в\s+гугле|в\s+поиске\s+гугл|нашел\s+в\s+яндекс', client_text):
        return "Поисковые системы"

    return "WhatsApp (Прямой)"

def extract_detailed_outcome(text, messages, row, operator_present):
    """
    Классифицирует точный результат и исход диалога:
    - Продажа / Оформлен заказ (выставлен счет, создана бронь с номером, чек, оплачено)
    - Запись на сервис / Услугу (шиномонтаж, балансировка, сезонное хранение, геометрия)
    - Отказ / Клиент ушел
    - Консультация менеджера / В работе у менеджера
    - Консультация AI-бота
    """
    text_lower = text.lower()
    deal = str(row.get("Сделка") or "").strip()
    deal_id = str(row.get("Сделка - ID") or "").strip()
    
    # 1. Проверка записи на сервис и услуги
    is_service_booked = (
        bool(re.search(r'записал(?:и|а)?\s+вас', text_lower)) or
        bool(re.search(r'запись\s+на\s+(?:шиномонтаж|сервис|установку|балансировку|замену|хранение|геометрию|развал)', text_lower)) or
        bool(re.search(r'будем\s+ждать\s+вас\s+на\s+шиномонтаж', text_lower)) or
        bool(re.search(r'записали\s+на\s+\d+', text_lower)) or
        bool(re.search(r'на\s+хранение\s+приняли', text_lower)) or
        bool(re.search(r'время\s+записи\s+подтвержден', text_lower))
    )
    
    # 2. Проверка успешной продажи / оформленного заказа
    is_sale_completed = (
        bool(deal or deal_id) or
        bool(re.search(r'номер\s+брони', text_lower)) or
        bool(re.search(r'заказ\s+(?:№|#|номер)\s*\d+', text_lower)) or
        bool(re.search(r'счет\s+на\s+оплату', text_lower)) or
        bool(re.search(r'выставил(?:а)?\s+счет', text_lower)) or
        bool(re.search(r'чек\s+об\s+оплат', text_lower)) or
        bool(re.search(r'оплатил[аи]?', text_lower)) or
        bool(re.search(r'оплачен[оа]?', text_lower)) or
        bool(re.search(r'чек\s+(?:скинул|отправил|прикрепил)', text_lower)) or
        bool(re.search(r'забронировал(?:и|а)?', text_lower)) or
        bool(re.search(r'товар\s+отложен', text_lower)) or
        bool(re.search(r'приеду\s+заберу', text_lower)) or
        bool(re.search(r'оформил(?:и|а)?\s+заказ', text_lower)) or
        bool(re.search(r'спасибо\s+за\s+покупку', text_lower)) or
        bool(re.search(r'договор\s+купли', text_lower)) or
        bool(re.search(r'оформили\s+на\s+ваше\s+имя', text_lower))
    )
    
    # 3. Проверка отказа
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
        if any(w in text_lower for w in ["хорошего дня", "всего доброго", "пожалуйста", "до свидания", "рады помочь", "обращайтесь"]):
            return "Консультация менеджера", "consultation"
        return "В работе у менеджера", "in_progress"
    if any(w in text_lower for w in ["айкос", "каталог", "https://aikos.kz"]):
        return "Консультация AI-бота", "bot"
        
    return "Завершен диалог", "closed"

def process_csv_file(csv_path, existing_sids=None):
    """Парсит один CSV файл выгрузки Открытых Линий Bitrix24."""
    if existing_sids is None:
        existing_sids = set()

    dialogs = []
    logging.info(f"Чтение файла выгрузки: {os.path.basename(csv_path)}")

    with open(csv_path, mode='r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f, delimiter=';')
        
        for row in reader:
            sid_key = next((k for k in row.keys() if 'SID' in k), None)
            if not sid_key:
                continue

            sid = str(row.get(sid_key) or "").strip()
            if not sid or sid in existing_sids:
                continue

            channel = str(row.get("Канал") or "WhatsApp").strip()
            line_name = str(row.get("Открытая линия") or "AI ассистент").strip()
            assigned_employee = str(row.get("Сотрудник") or "").strip()
            client_name = str(row.get("Клиент") or "Клиент").strip()
            created_at = str(row.get("Дата создания") or "").strip()
            closed_at = str(row.get("Дата последнего сообщения") or "").strip()
            try:
                msg_count = int(row.get("Количество сообщений") or 0)
            except (ValueError, TypeError):
                msg_count = 0
            raw_text = str(row.get("Текст переписки") or "")

            # Извлечение структурированных сообщений
            messages = parse_dialog_messages(raw_text)

            # Проверка участия оператора
            operator_messages = [m for m in messages if m["role"] == "operator"]
            bot_messages = [m for m in messages if m["role"] == "bot"]
            client_messages = [m for m in messages if m["role"] == "client"]
            
            operator_present = len(operator_messages) > 0
            operator_name = operator_messages[0]["sender"] if operator_present else (
                assigned_employee if assigned_employee and assigned_employee != "AI ассистент" else "Только AI-бот"
            )
            
            # Извлечение аналитических признаков
            city = extract_city(raw_text, messages)
            tire_sizes = extract_tire_sizes(raw_text)
            brands = extract_brands(raw_text)
            car_model = extract_car_model(raw_text)
            category = extract_product_category(raw_text, tire_sizes, brands)
            intents = extract_intents(raw_text)
            source = extract_source(raw_text, messages, row)
            outcome_label, outcome_type = extract_detailed_outcome(raw_text, messages, row, operator_present)

            # Вычисление длительности диалога в минутах
            duration_min = 0.0
            try:
                if created_at and closed_at:
                    dt_start = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                    dt_end = datetime.strptime(closed_at, "%Y-%m-%d %H:%M:%S")
                    duration_min = max(round((dt_end - dt_start).total_seconds() / 60, 1), 0.1)
            except Exception:
                duration_min = 1.0

            dialogs.append({
                "sid": sid,
                "client": client_name,
                "channel": channel,
                "lineName": line_name,
                "createdAt": created_at,
                "closedAt": closed_at,
                "durationMin": duration_min,
                "messagesCount": msg_count or len(messages),
                "clientMsgCount": len(client_messages),
                "botMsgCount": len(bot_messages),
                "operatorMsgCount": len(operator_messages),
                "operatorPresent": operator_present,
                "operator": operator_name,
                "city": city,
                "source": source,
                "tireSizes": tire_sizes,
                "primarySize": tire_sizes[0] if tire_sizes else "—",
                "brands": brands,
                "primaryBrand": brands[0] if brands else "—",
                "carModel": car_model or "—",
                "category": category,
                "intents": intents,
                "primaryIntent": intents[0] if intents else "Консультация",
                "outcome": outcome_label,
                "outcomeType": outcome_type,
                "isSale": outcome_type == "sale",
                "isService": outcome_type == "service",
                "rawTextSnippet": raw_text[:300] + "..." if len(raw_text) > 300 else raw_text,
                "messages": messages
            })
            existing_sids.add(sid)

    logging.info(f"Успешно обработано {len(dialogs)} новых диалогов из {os.path.basename(csv_path)}.")
    return dialogs

def compute_aggregates(dialogs):
    """Рассчитывает сводные показатели и данные для графиков дашборда."""
    total_dialogs = len(dialogs)
    if total_dialogs == 0:
        return {}

    cities_counter = Counter(d.get("city", "Не определен") for d in dialogs)
    categories_counter = Counter(d.get("category", "Шины (Общее)") for d in dialogs)
    sources_counter = Counter(d.get("source", "WhatsApp (Прямой)") for d in dialogs)
    outcomes_counter = Counter(d.get("outcome", "Завершен диалог") for d in dialogs)
    
    # Все типоразмеры
    sizes_counter = Counter()
    for d in dialogs:
        for s in d.get("tireSizes", []):
            sizes_counter[s] += 1

    # Все бренды
    brands_counter = Counter()
    for d in dialogs:
        for b in d.get("brands", []):
            brands_counter[b] += 1

    # Все темы вопросов
    intents_counter = Counter()
    for d in dialogs:
        for it in d.get("intents", []):
            intents_counter[it] += 1

    # Операторы и конверсии
    operators_counter = Counter(d.get("operator", "AI-бот") for d in dialogs if d.get("operatorPresent"))
    bot_only_count = sum(1 for d in dialogs if not d.get("operatorPresent"))
    operator_escalated_count = sum(1 for d in dialogs if d.get("operatorPresent"))

    # Продажи и сервисные записи
    sales_count = sum(1 for d in dialogs if d.get("isSale"))
    service_count = sum(1 for d in dialogs if d.get("isService"))
    total_conversions = sales_count + service_count

    # Статистика по менеджерам
    manager_breakdown = defaultdict(lambda: {"total": 0, "sales": 0, "service": 0, "consult": 0})
    for d in dialogs:
        if d.get("operatorPresent"):
            op = d.get("operator", "Оператор")
            manager_breakdown[op]["total"] += 1
            if d.get("isSale"):
                manager_breakdown[op]["sales"] += 1
            elif d.get("isService"):
                manager_breakdown[op]["service"] += 1
            else:
                manager_breakdown[op]["consult"] += 1

    manager_perf = []
    for op, stats in sorted(manager_breakdown.items(), key=lambda x: x[1]["total"], reverse=True):
        cr = round(((stats["sales"] + stats["service"]) / stats["total"]) * 100, 1) if stats["total"] > 0 else 0
        manager_perf.append({
            "name": op,
            "total": stats["total"],
            "sales": stats["sales"],
            "service": stats["service"],
            "consult": stats["consult"],
            "conversionRate": cr
        })

    # Подсчет сессий на клиента (исключая безликих "Клиент", "Гость")
    named_client_counts = Counter(d.get("client", "") for d in dialogs if d.get("client") not in ["Клиент", "Гость", "", "—"])
    for d in dialogs:
        cl = d.get("client", "")
        d["clientTotalSessions"] = named_client_counts.get(cl, 1) if cl not in ["Клиент", "Гость", "", "—"] else 1

    unique_clients = len(set(d.get("client", "") for d in dialogs))
    repeat_clients_count = sum(1 for c, cnt in named_client_counts.items() if cnt > 1)
    repeat_clients_pct = round((repeat_clients_count / unique_clients) * 100, 1) if unique_clients else 0

    # Статистика по датам
    dates_counter = Counter(d.get("createdAt", "")[:10] for d in dialogs if d.get("createdAt"))

    return {
        "summary": {
            "totalDialogs": total_dialogs,
            "uniqueClients": unique_clients,
            "repeatClientsCount": repeat_clients_count,
            "repeatClientsPct": repeat_clients_pct,
            "salesCount": sales_count,
            "salesPct": round((sales_count / total_dialogs) * 100, 1),
            "serviceCount": service_count,
            "servicePct": round((service_count / total_dialogs) * 100, 1),
            "totalConversions": total_conversions,
            "conversionRate": round((total_conversions / total_dialogs) * 100, 1),
            "botOnlyCount": bot_only_count,
            "botOnlyPct": round((bot_only_count / total_dialogs) * 100, 1),
            "operatorEscalatedCount": operator_escalated_count,
            "operatorEscalatedPct": round((operator_escalated_count / total_dialogs) * 100, 1),
            "withCityCount": sum(1 for d in dialogs if d["city"] != "Не определен"),
            "withTireSizeCount": sum(1 for d in dialogs if len(d["tireSizes"]) > 0),
            "totalMessages": sum(d["messagesCount"] for d in dialogs),
            "avgMessagesPerDialog": round(sum(d["messagesCount"] for d in dialogs) / total_dialogs, 1),
        },
        "charts": {
            "sources": [
                {"source": k, "count": v, "pct": round((v / total_dialogs) * 100, 1)} 
                for k, v in sources_counter.most_common()
            ],
            "outcomes": [
                {"outcome": k, "count": v, "pct": round((v / total_dialogs) * 100, 1)} 
                for k, v in outcomes_counter.most_common()
            ],
            "managerPerformance": manager_perf,
            "cities": [
                {"city": k, "count": v, "pct": round((v / total_dialogs) * 100, 1)} 
                for k, v in cities_counter.most_common(12)
            ],
            "categories": [
                {"category": k, "count": v, "pct": round((v / total_dialogs) * 100, 1)} 
                for k, v in categories_counter.most_common()
            ],
            "topSizes": [
                {"size": k, "count": v} 
                for k, v in sizes_counter.most_common(15)
            ],
            "topBrands": [
                {"brand": k, "count": v} 
                for k, v in brands_counter.most_common(12)
            ],
            "intents": [
                {"intent": k, "count": v, "pct": round((v / total_dialogs) * 100, 1)} 
                for k, v in intents_counter.most_common()
            ],
            "operators": [
                {"name": k, "count": v} 
                for k, v in operators_counter.most_common()
            ],
            "timeline": [
                {"date": k, "count": v} 
                for k, v in sorted(dates_counter.items())
            ]
        }
    }

def generate_dashboard_html(data=None):
    """Генерирует автономный интерактивный дашборд аналитики диалогов с поддержкой источников и конверсий."""
    embedded_json = json.dumps(data, ensure_ascii=False) if data else "null"
    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AIKOS Intelligence — Аналитика диалогов Bitrix24 WhatsApp</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-base: #0c0f17;
            --bg-card: rgba(21, 26, 38, 0.85);
            --bg-card-hover: rgba(30, 38, 56, 0.95);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --accent-blue: #3b82f6;
            --accent-cyan: #06b6d4;
            --accent-purple: #8b5cf6;
            --accent-emerald: #10b981;
            --accent-amber: #f59e0b;
            --accent-rose: #f43f5e;
            --wa-green: #25d366;
            --wa-dark: #128c7e;
            --wa-bg: #0b141a;
            --wa-bubble-client: #005c4b;
            --wa-bubble-bot: #202c33;
            --wa-bubble-op: #322659;
            --radius-sm: 8px;
            --radius-md: 14px;
            --radius-lg: 20px;
            --shadow-glow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Inter', sans-serif; }}
        body {{ background: var(--bg-base); color: var(--text-main); min-height: 100vh; overflow-x: hidden; }}

        /* Шапка */
        header {{
            background: linear-gradient(180deg, rgba(17, 24, 39, 0.9) 0%, rgba(12, 15, 23, 0.8) 100%);
            border-bottom: 1px solid var(--border-color);
            padding: 18px 32px;
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(16px);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .brand-block {{ display: flex; align-items: center; gap: 14px; }}
        .logo-icon {{
            width: 44px; height: 44px; border-radius: 12px;
            background: linear-gradient(135deg, #10b981 0%, #06b6d4 100%);
            display: flex; align-items: center; justify-content: center;
            font-size: 24px; box-shadow: 0 4px 20px rgba(16, 185, 129, 0.4);
        }}
        .brand-text h1 {{ font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }}
        .brand-text p {{ font-size: 13px; color: var(--text-muted); font-weight: 400; }}
        
        .header-actions {{ display: flex; gap: 12px; align-items: center; }}
        .badge-live {{
            background: rgba(16, 185, 129, 0.15); color: #34d399;
            padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600;
            display: flex; align-items: center; gap: 6px; border: 1px solid rgba(16, 185, 129, 0.3);
        }}
        .badge-live::before {{ content: ""; width: 8px; height: 8px; background: #10b981; border-radius: 50%; box-shadow: 0 0 10px #10b981; }}

        /* Контейнер */
        .container {{ max-width: 1560px; margin: 0 auto; padding: 24px 32px 60px; }}

        /* Сетка KPI карточек */
        .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 28px; }}
        .kpi-card {{
            background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: var(--radius-md); padding: 18px 20px; backdrop-filter: blur(12px);
            transition: transform 0.2s ease, border-color 0.2s ease;
            position: relative; overflow: hidden;
        }}
        .kpi-card:hover {{ transform: translateY(-3px); border-color: rgba(255, 255, 255, 0.2); }}
        .kpi-card::after {{
            content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
            background: linear-gradient(90deg, var(--accent-cyan), var(--accent-blue));
        }}
        .kpi-card.kpi-sale::after {{ background: linear-gradient(90deg, #10b981, #059669); }}
        .kpi-card.kpi-service::after {{ background: linear-gradient(90deg, #06b6d4, #3b82f6); }}
        .kpi-card.kpi-cr::after {{ background: linear-gradient(90deg, #f59e0b, #ef4444); }}

        .kpi-title {{ font-size: 13px; color: var(--text-muted); font-weight: 500; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }}
        .kpi-value {{ font-size: 26px; font-weight: 800; letter-spacing: -0.5px; }}
        .kpi-sub {{ font-size: 12px; color: var(--accent-emerald); font-weight: 600; margin-top: 6px; display: flex; align-items: center; gap: 4px; }}

        /* Сетка графиков */
        .charts-grid {{ display: grid; grid-template-columns: repeat(12, 1fr); gap: 20px; margin-bottom: 28px; }}
        .chart-card {{
            background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: var(--radius-md); padding: 22px; backdrop-filter: blur(12px);
        }}
        .chart-card h3 {{ font-size: 15px; font-weight: 700; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }}
        .col-4 {{ grid-column: span 4; }}
        .col-6 {{ grid-column: span 6; }}
        .col-8 {{ grid-column: span 8; }}
        .col-12 {{ grid-column: span 12; }}

        .chart-container {{ position: relative; height: 280px; width: 100%; }}

        /* Панель фильтров */
        .filter-panel {{
            background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: var(--radius-md); padding: 18px 24px; margin-bottom: 20px;
            display: flex; flex-wrap: wrap; gap: 14px; align-items: center; justify-content: space-between;
        }}
        .filter-group {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
        .search-box {{
            background: rgba(0, 0, 0, 0.3); border: 1px solid var(--border-color);
            border-radius: 10px; padding: 10px 16px; color: white; width: 280px; font-size: 13.5px;
            outline: none; transition: border-color 0.2s;
        }}
        .search-box:focus {{ border-color: var(--accent-blue); box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2); }}
        .select-filter {{
            background: rgba(0, 0, 0, 0.3); border: 1px solid var(--border-color);
            border-radius: 10px; padding: 9px 12px; color: var(--text-main); font-size: 13px;
            outline: none; cursor: pointer;
        }}
        .select-filter option {{ background: #1e293b; color: white; }}
        
        .date-range-box {{
            display: flex; align-items: center; gap: 6px; background: rgba(0, 0, 0, 0.3);
            border: 1px solid var(--border-color); border-radius: 10px; padding: 4px 10px;
            font-size: 12px; color: var(--text-muted);
        }}
        .date-input {{
            background: transparent; border: none; color: var(--text-main); font-size: 13px;
            outline: none; color-scheme: dark; font-family: inherit; cursor: pointer;
        }}
        .btn-reset {{
            background: rgba(244, 63, 94, 0.15); color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.3);
            padding: 9px 14px; border-radius: 10px; font-size: 13px; font-weight: 600; cursor: pointer;
            transition: all 0.2s ease;
        }}
        .btn-reset:hover {{
            background: rgba(244, 63, 94, 0.25); border-color: rgba(244, 63, 94, 0.5);
        }}

        /* Таблица обращений */
        .table-card {{
            background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: var(--radius-md); overflow-x: auto; backdrop-filter: blur(12px);
        }}
        .custom-table {{ width: 100%; border-collapse: collapse; text-align: left; min-width: 1200px; }}
        .custom-table th {{
            background: rgba(0, 0, 0, 0.35); padding: 14px 16px; font-size: 11.5px;
            text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); font-weight: 600;
            border-bottom: 1px solid var(--border-color);
        }}
        .custom-table td {{
            padding: 14px 16px; font-size: 13px; border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            vertical-align: middle;
        }}
        .custom-table tr:hover td {{ background: rgba(255, 255, 255, 0.03); cursor: pointer; }}

        /* Бейджи */
        .badge {{
            display: inline-flex; align-items: center; gap: 4px; padding: 4px 9px; border-radius: 6px; font-size: 11px;
            font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px; white-space: nowrap;
        }}
        .badge-source {{ background: rgba(14, 165, 233, 0.15); color: #38bdf8; border: 1px solid rgba(14, 165, 233, 0.3); }}
        .badge-source-2gis {{ background: rgba(34, 197, 94, 0.15); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.3); }}
        .badge-source-kaspi {{ background: rgba(244, 63, 94, 0.15); color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.3); }}
        .badge-source-kolesa {{ background: rgba(245, 158, 11, 0.15); color: #fcd34d; border: 1px solid rgba(245, 158, 11, 0.3); }}
        .badge-source-insta {{ background: rgba(217, 70, 239, 0.15); color: #e879f9; border: 1px solid rgba(217, 70, 239, 0.3); }}

        .badge-city {{ background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }}
        .badge-size {{ background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); font-weight: 700; }}
        .badge-brand {{ background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }}
        .badge-op {{ background: rgba(139, 92, 246, 0.15); color: #a78bfa; border: 1px solid rgba(139, 92, 246, 0.3); }}
        .badge-bot {{ background: rgba(148, 163, 184, 0.15); color: #cbd5e1; }}
        
        .badge-sale {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.5); font-weight: 700; }}
        .badge-service {{ background: rgba(6, 182, 212, 0.2); color: #22d3ee; border: 1px solid rgba(6, 182, 212, 0.5); font-weight: 700; }}
        .badge-consult {{ background: rgba(139, 92, 246, 0.15); color: #c084fc; }}
        .badge-progress {{ background: rgba(245, 158, 11, 0.15); color: #fcd34d; }}
        .badge-lost {{ background: rgba(244, 63, 94, 0.15); color: #fda4af; }}
        .badge-repeat {{ background: rgba(168, 85, 247, 0.18); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.35); font-size: 10.5px; }}

        /* Пагинация */
        .btn-page {{
            background: rgba(255, 255, 255, 0.06); border: 1px solid var(--border-color);
            color: var(--text-main); padding: 6px 14px; border-radius: 8px; font-size: 12.5px;
            font-weight: 600; cursor: pointer; transition: all 0.2s;
        }}
        .btn-page:hover:not(:disabled) {{ background: var(--accent-blue); color: white; }}
        .btn-page:disabled {{ opacity: 0.35; cursor: not-allowed; }}
        .page-num {{
            background: rgba(255, 255, 255, 0.04); border: 1px solid var(--border-color);
            color: var(--text-muted); min-width: 32px; height: 32px; padding: 0 6px;
            border-radius: 8px; font-size: 12.5px; font-weight: 600; cursor: pointer;
            display: inline-flex; align-items: center; justify-content: center; transition: all 0.2s;
        }}
        .page-num:hover {{ background: rgba(255, 255, 255, 0.1); color: white; }}
        .page-num.active {{ background: var(--accent-blue); color: white; border-color: var(--accent-blue); }}

        /* Сессии и разделители */
        .session-nav-bar {{
            display: none; padding: 10px 20px; background: #182229;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08); gap: 8px;
            overflow-x: auto; align-items: center; scrollbar-width: thin;
        }}
        .session-tab-btn {{
            background: rgba(255, 255, 255, 0.06); border: 1px solid rgba(255, 255, 255, 0.1);
            color: var(--text-muted); padding: 5px 12px; border-radius: 16px; font-size: 11.5px;
            font-weight: 600; cursor: pointer; white-space: nowrap; transition: all 0.2s;
        }}
        .session-tab-btn:hover {{ background: rgba(255, 255, 255, 0.12); color: white; }}
        .session-tab-btn.active {{ background: var(--accent-blue); color: white; border-color: var(--accent-blue); }}

        .session-divider {{
            display: flex; justify-content: center; margin: 24px 0 14px; position: relative; width: 100%;
        }}
        .session-divider::before {{
            content: ""; position: absolute; top: 50%; left: 0; right: 0; height: 1px;
            background: rgba(255, 255, 255, 0.1); z-index: 1;
        }}
        .session-divider-badge {{
            position: relative; z-index: 2; background: #1f2c34;
            border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 20px;
            padding: 6px 14px; font-size: 11.5px; color: #94a3b8;
            display: flex; align-items: center; gap: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            flex-wrap: wrap; justify-content: center;
        }}

        /* Модальное окно чата (WhatsApp Style) */
        .modal-overlay {{
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.75); backdrop-filter: blur(8px);
            display: none; justify-content: center; align-items: center; z-index: 1000;
        }}
        .modal-dialog {{
            background: var(--wa-bg); border: 1px solid var(--border-color);
            border-radius: var(--radius-lg); width: 90%; max-width: 860px; height: 86vh;
            display: flex; flex-direction: column; overflow: hidden; box-shadow: var(--shadow-glow);
        }}
        .modal-header {{
            background: #202c33; padding: 16px 24px; display: flex; justify-content: space-between;
            align-items: center; border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        }}
        .chat-user-info {{ display: flex; align-items: center; gap: 14px; }}
        .chat-avatar {{
            width: 44px; height: 44px; border-radius: 50%; background: #00a884;
            display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: 700;
        }}
        .chat-meta h4 {{ font-size: 16px; font-weight: 700; }}
        .chat-meta p {{ font-size: 12px; color: var(--text-muted); display: flex; gap: 8px; align-items: center; margin-top: 2px; }}
        .close-btn {{ background: none; border: none; color: var(--text-muted); font-size: 24px; cursor: pointer; }}
        .close-btn:hover {{ color: white; }}

        .chat-body {{
            flex: 1; padding: 24px; overflow-y: auto; display: flex; flex-direction: column;
            gap: 12px; background: radial-gradient(circle, rgba(18, 140, 126, 0.04) 0%, transparent 100%);
        }}
        .chat-bubble {{
            max-width: 75%; padding: 10px 14px; border-radius: 12px; font-size: 13.5px;
            line-height: 1.5; position: relative; word-break: break-word;
        }}
        .bubble-client {{
            background: var(--wa-bubble-client); align-self: flex-start;
            border-top-left-radius: 2px; color: #e9edef;
        }}
        .bubble-bot {{
            background: var(--wa-bubble-bot); align-self: flex-end;
            border-top-right-radius: 2px; color: #e9edef; border-left: 3px solid var(--accent-cyan);
        }}
        .bubble-operator {{
            background: var(--wa-bubble-op); align-self: flex-end;
            border-top-right-radius: 2px; color: #e9edef; border-left: 3px solid var(--accent-purple);
        }}
        .bubble-sender {{ font-size: 11px; font-weight: 700; margin-bottom: 4px; opacity: 0.85; }}
        .bubble-time {{ font-size: 10px; color: rgba(255, 255, 255, 0.5); text-align: right; margin-top: 4px; }}

        .modal-footer {{
            background: #202c33; padding: 14px 24px; border-top: 1px solid rgba(255, 255, 255, 0.08);
            display: flex; gap: 12px; font-size: 12px; color: var(--text-muted); justify-content: space-between;
        }}
    </style>
</head>
<body>

<header>
    <div class="brand-block">
        <div class="logo-icon">💬</div>
        <div class="brand-text">
            <h1>AIKOS Dialog Intelligence</h1>
            <p>Анализ обращений, источников и конверсий Bitrix24 WhatsApp</p>
        </div>
    </div>
    <div class="header-actions">
        <div class="badge-live">Live Analytics</div>
        <a href="/" style="text-decoration:none; color:white; background:rgba(255,255,255,0.08); padding:8px 16px; border-radius:8px; font-size:13px; font-weight:600; transition:0.2s;">🎙️ Перейти к Звонкам</a>
    </div>
</header>

<div class="container">
    <!-- KPI Карточки -->
    <div class="kpi-grid" id="kpiGrid">
        <div class="kpi-card">
            <div class="kpi-title">Всего диалогов</div>
            <div class="kpi-value" id="kpiTotal">0</div>
            <div class="kpi-sub">100% из WhatsApp</div>
        </div>
        <div class="kpi-card kpi-sale">
            <div class="kpi-title">🏆 Продажи / Заказы</div>
            <div class="kpi-value" id="kpiSales" style="color:#34d399;">0</div>
            <div class="kpi-sub" id="kpiSalesPct" style="color:#34d399;">0% от всех</div>
        </div>
        <div class="kpi-card kpi-service">
            <div class="kpi-title">🔧 Записи на сервис</div>
            <div class="kpi-value" id="kpiService" style="color:#22d3ee;">0</div>
            <div class="kpi-sub" id="kpiServicePct" style="color:#22d3ee;">0% от всех</div>
        </div>
        <div class="kpi-card kpi-cr">
            <div class="kpi-title">🎯 Общая конверсия</div>
            <div class="kpi-value" id="kpiCR" style="color:#fbbf24;">0%</div>
            <div class="kpi-sub" id="kpiCRCount" style="color:#fbbf24;">Продажи + Сервис</div>
        </div>
        <div class="kpi-card" style="border-left: 3px solid #a855f7;">
            <div class="kpi-title">🔁 Повторные клиенты</div>
            <div class="kpi-value" id="kpiRepeatCount" style="color:#c084fc;">0</div>
            <div class="kpi-sub" id="kpiRepeatPct" style="color:#c084fc;">0% базы (LTV)</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">🤖 AI-бот (Автономно)</div>
            <div class="kpi-value" id="kpiBotPct">0%</div>
            <div class="kpi-sub" id="kpiBotCount">0 диалогов</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">👨‍💼 На операторах</div>
            <div class="kpi-value" id="kpiOpPct">0%</div>
            <div class="kpi-sub" id="kpiOpCount">0 диалогов</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">📍 Город определен</div>
            <div class="kpi-value" id="kpiCityCount">0</div>
            <div class="kpi-sub">Геолокация клиентов</div>
        </div>
    </div>

    <!-- Графики -->
    <div class="charts-grid">
        <div class="chart-card col-4">
            <h3>🌐 Источники обращений (Traffic Channels)</h3>
            <div class="chart-container"><canvas id="sourceChart"></canvas></div>
        </div>
        <div class="chart-card col-4">
            <h3>🎯 Результаты и исходы диалогов</h3>
            <div class="chart-container"><canvas id="outcomeChart"></canvas></div>
        </div>
        <div class="chart-card col-4">
            <h3>📍 География клиентов</h3>
            <div class="chart-container"><canvas id="cityChart"></canvas></div>
        </div>
        <div class="chart-card col-6">
            <h3>👨‍💼 Результативность менеджеров (Продажи / Сервис / Консультации)</h3>
            <div class="chart-container"><canvas id="managerChart"></canvas></div>
        </div>
        <div class="chart-card col-6">
            <h3>🚗 Топ популярных типоразмеров шин и дисков</h3>
            <div class="chart-container"><canvas id="sizeChart"></canvas></div>
        </div>
        <div class="chart-card col-8">
            <h3>❓ Тематика и вопросы клиентов</h3>
            <div class="chart-container"><canvas id="intentChart"></canvas></div>
        </div>
        <div class="chart-card col-4">
            <h3>🏷️ Популярные бренды</h3>
            <div class="chart-container"><canvas id="brandChart"></canvas></div>
        </div>
    </div>

    <!-- Фильтры -->
    <div class="filter-panel">
        <div class="filter-group">
            <input type="text" id="searchInput" class="search-box" placeholder="🔍 Поиск по клиенту, тексту, размеру, источнику...">
            
            <select id="datePresetFilter" class="select-filter" title="Выбор месяца / периода">
                <option value="">📅 Все периоды</option>
            </select>

            <div class="date-range-box" title="Произвольный диапазон дат">
                <span>С:</span>
                <input type="date" id="dateFrom" class="date-input">
                <span>По:</span>
                <input type="date" id="dateTo" class="date-input">
            </div>

            <select id="sourceFilter" class="select-filter">
                <option value="">🌐 Все источники</option>
            </select>

            <select id="outcomeFilter" class="select-filter">
                <option value="">🎯 Все исходы</option>
                <option value="sale">🏆 Только Продажи / Заказы</option>
                <option value="service">🔧 Только Записи на сервис</option>
                <option value="consultation">💬 Консультации менеджеров</option>
                <option value="in_progress">⏳ В работе</option>
                <option value="lost">❌ Отказы / Ушли</option>
                <option value="bot">🤖 Только AI-бот</option>
            </select>

            <select id="cityFilter" class="select-filter">
                <option value="">Все города</option>
            </select>

            <select id="operatorFilter" class="select-filter">
                <option value="">Все менеджеры</option>
            </select>

            <select id="categoryFilter" class="select-filter">
                <option value="">Все категории</option>
            </select>

            <button type="button" id="resetFiltersBtn" class="btn-reset">Сбросить</button>
        </div>
        <div style="font-size: 13px; color: var(--text-muted);" id="counterLabel">Найдено: 0</div>
    </div>

    <!-- Таблица обращений -->
    <div class="table-card">
        <table class="custom-table">
            <thead>
                <tr>
                    <th>SID / Дата</th>
                    <th>Клиент</th>
                    <th>Источник</th>
                    <th>Город</th>
                    <th>Категория</th>
                    <th>Типоразмер</th>
                    <th>Бренд / Авто</th>
                    <th>Менеджер</th>
                    <th>Результат / Исход</th>
                    <th>Тема</th>
                    <th>Сообщений</th>
                </tr>
            </thead>
            <tbody id="dialogsTableBody">
                <tr><td colspan="11" style="text-align:center; padding: 40px; color: var(--text-muted);">Загрузка аналитики диалогов...</td></tr>
            </tbody>
        </table>
        
        <!-- Пагинация -->
        <div class="pagination-bar" id="paginationBar" style="display:flex; justify-content:space-between; align-items:center; padding:16px 20px; background:rgba(0,0,0,0.25); border-top:1px solid var(--border-color); flex-wrap:wrap; gap:12px;">
            <div style="font-size:13px; color:var(--text-muted);" id="pageInfo">Показано 1–50 из 0</div>
            <div style="display:flex; gap:6px; align-items:center;">
                <button id="prevPageBtn" class="btn-page" onclick="changePage(-1)">← Назад</button>
                <div id="pageNumbers" style="display:flex; gap:4px;"></div>
                <button id="nextPageBtn" class="btn-page" onclick="changePage(1)">Вперед →</button>
            </div>
        </div>
    </div>
</div>

<!-- Модальное окно чата -->
<div class="modal-overlay" id="chatModal">
    <div class="modal-dialog">
        <div class="modal-header">
            <div class="chat-user-info">
                <div class="chat-avatar" id="modalAvatar">💬</div>
                <div class="chat-meta">
                    <h4 id="modalClientName">Имя клиента</h4>
                    <p id="modalSubInfo">Караганда • 2GIS • Зимние шины</p>
                </div>
            </div>
            <button class="close-btn" onclick="closeChatModal()">✕</button>
        </div>
        <div class="session-nav-bar" id="modalSessionNav"></div>
        <div class="chat-body" id="chatBody"></div>
        <div class="modal-footer">
            <span id="modalMetaLeft">SID: 42488</span>
            <span id="modalMetaRight">Оператор: Айнур Усманова</span>
        </div>
    </div>
</div>

<script>
window.__EMBEDDED_DATA__ = {embedded_json};

let rawData = null;
let allDialogs = [];
let charts = {{}};

async function initDashboard() {{
    try {{
        if (window.__EMBEDDED_DATA__) {{
            rawData = window.__EMBEDDED_DATA__;
        }} else {{
            const res = await fetch("dialogs_data.json?t=" + new Date().getTime());
            if (!res.ok) throw new Error("Не удалось загрузить dialogs_data.json");
            rawData = await res.json();
        }}
        allDialogs = rawData.dialogs || [];
        
        renderKPIs(rawData.aggregates.summary);
        initCharts(rawData.aggregates.charts);
        populateFilters(rawData.aggregates.charts);
        populateDatePresets(allDialogs);
        renderTable(allDialogs);
        setupEventListeners();
    }} catch (err) {{
        console.error("Ошибка загрузки:", err);
        document.getElementById("dialogsTableBody").innerHTML = `<tr><td colspan="11" style="text-align:center; padding: 40px; color:#f43f5e;">Ошибка загрузки данных: ${{err.message}}</td></tr>`;
    }}
}}

function renderKPIs(summary) {{
    if (!summary) return;
    document.getElementById("kpiTotal").innerText = summary.totalDialogs || 0;
    
    // Продажи и Сервис
    const sales = summary.salesCount || 0;
    const service = summary.serviceCount || 0;
    const cr = summary.conversionRate || 0;
    
    document.getElementById("kpiSales").innerText = sales;
    document.getElementById("kpiSalesPct").innerText = `${{summary.salesPct || 0}}% от обращений`;
    
    document.getElementById("kpiService").innerText = service;
    document.getElementById("kpiServicePct").innerText = `${{summary.servicePct || 0}}% от обращений`;

    document.getElementById("kpiCR").innerText = cr + "%";
    document.getElementById("kpiCRCount").innerText = `${{summary.totalConversions || (sales + service)}} целевых действий`;

    document.getElementById("kpiRepeatCount").innerText = summary.repeatClientsCount || 0;
    document.getElementById("kpiRepeatPct").innerText = `${{summary.repeatClientsPct || 0}}% базы (LTV)`;

    document.getElementById("kpiBotPct").innerText = (summary.botOnlyPct || 0) + "%";
    document.getElementById("kpiBotCount").innerText = `${{summary.botOnlyCount || 0}} диалогов закрыто ботом`;
    document.getElementById("kpiOpPct").innerText = (summary.operatorEscalatedPct || 0) + "%";
    document.getElementById("kpiOpCount").innerText = `${{summary.operatorEscalatedCount || 0}} с менеджером`;
    document.getElementById("kpiCityCount").innerText = summary.withCityCount || 0;
}}

function getSourceBadgeHtml(source) {{
    if (!source) return `<span class="badge badge-source">WhatsApp</span>`;
    if (source.includes("2GIS")) return `<span class="badge badge-source-2gis">🗺️ 2GIS</span>`;
    if (source.includes("Kaspi")) return `<span class="badge badge-source-kaspi">💳 Kaspi</span>`;
    if (source.includes("Kolesa")) return `<span class="badge badge-source-kolesa">🚗 Kolesa.kz</span>`;
    if (source.includes("Instagram")) return `<span class="badge badge-source-insta">📸 Instagram</span>`;
    if (source.includes("Сайт")) return `<span class="badge badge-source">🌐 Сайт Aikos</span>`;
    if (source.includes("Яндекс")) return `<span class="badge badge-source" style="background:rgba(239,68,68,0.15); color:#f87171;">📍 Яндекс</span>`;
    if (source.includes("Google")) return `<span class="badge badge-source" style="background:rgba(59,130,246,0.15); color:#60a5fa;">🔍 Google</span>`;
    return `<span class="badge badge-source">💬 ${{source}}</span>`;
}}

function getOutcomeBadgeHtml(outcome, isSale, isService) {{
    if (isSale || outcome.includes("Продажа")) {{{{
        return `<span class="badge badge-sale">🏆 Продажа / Заказ</span>`;
    }}}}
    if (isService || outcome.includes("Запись на сервис")) {{{{
        return `<span class="badge badge-service">🔧 Запись на сервис</span>`;
    }}}}
    if (outcome.includes("Отказ")) {{{{
        return `<span class="badge badge-lost">❌ Отказ / Ушел</span>`;
    }}}}
    if (outcome.includes("В работе")) {{{{
        return `<span class="badge badge-progress">⏳ В работе</span>`;
    }}}}
    if (outcome.includes("Консультация менеджера")) {{{{
        return `<span class="badge badge-consult">💬 Консультация</span>`;
    }}}}
    if (outcome.includes("AI-бот")) {{{{
        return `<span class="badge badge-bot">🤖 AI-бот</span>`;
    }}}}
    return `<span class="badge badge-bot">${{outcome}}</span>`;
}}

function initCharts(chartsData) {{
    if (!chartsData) return;

    // 1. Источники
    const sourceLabels = chartsData.sources.map(s => s.source);
    const sourceValues = chartsData.sources.map(s => s.count);
    charts.source = new Chart(document.getElementById("sourceChart"), {{
        type: "doughnut",
        data: {{
            labels: sourceLabels,
            datasets: [{{
                data: sourceValues,
                backgroundColor: ["#3b82f6", "#10b981", "#06b6d4", "#f43f5e", "#f59e0b", "#d946ef", "#8b5cf6", "#64748b"]
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ position: "right", labels: {{ color: "#94a3b8", font: {{ size: 11 }} }} }} }}
        }}
    }});

    // 2. Исходы диалогов
    const outcomeLabels = chartsData.outcomes.map(o => o.outcome);
    const outcomeValues = chartsData.outcomes.map(o => o.count);
    charts.outcome = new Chart(document.getElementById("outcomeChart"), {{
        type: "doughnut",
        data: {{
            labels: outcomeLabels,
            datasets: [{{
                data: outcomeValues,
                backgroundColor: ["#8b5cf6", "#f59e0b", "#06b6d4", "#10b981", "#64748b", "#f43f5e", "#3b82f6"]
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ position: "right", labels: {{ color: "#94a3b8", font: {{ size: 11 }} }} }} }}
        }}
    }});

    // 3. Города
    const cityLabels = chartsData.cities.map(c => c.city);
    const cityValues = chartsData.cities.map(c => c.count);
    charts.city = new Chart(document.getElementById("cityChart"), {{
        type: "doughnut",
        data: {{
            labels: cityLabels,
            datasets: [{{
                data: cityValues,
                backgroundColor: ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#06b6d4", "#ec4899", "#64748b", "#f97316", "#14b8a6", "#a855f7"]
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ position: "right", labels: {{ color: "#94a3b8", font: {{ size: 11 }} }} }} }}
        }}
    }});

    // 4. Менеджеры: Продажи vs Сервис vs Консультации
    const managerPerf = chartsData.managerPerformance || [];
    const mgrNames = managerPerf.map(m => m.name.split(" ")[0] || m.name);
    charts.manager = new Chart(document.getElementById("managerChart"), {{
        type: "bar",
        data: {{
            labels: mgrNames,
            datasets: [
                {{
                    label: "🏆 Продажи",
                    data: managerPerf.map(m => m.sales),
                    backgroundColor: "#10b981",
                    borderRadius: 4
                }},
                {{
                    label: "🔧 Записи на сервис",
                    data: managerPerf.map(m => m.service),
                    backgroundColor: "#06b6d4",
                    borderRadius: 4
                }},
                {{
                    label: "💬 Консультации",
                    data: managerPerf.map(m => m.consult),
                    backgroundColor: "rgba(139, 92, 246, 0.6)",
                    borderRadius: 4
                }}
            ]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ position: "top", labels: {{ color: "#94a3b8" }} }} }},
            scales: {{
                x: {{ stacked: true, ticks: {{ color: "#94a3b8" }}, grid: {{ display: false }} }},
                y: {{ stacked: true, ticks: {{ color: "#94a3b8" }}, grid: {{ color: "rgba(255,255,255,0.05)" }} }}
            }}
        }}
    }});

    // 5. Топ типоразмеров
    const sizeLabels = chartsData.topSizes.map(s => s.size);
    const sizeValues = chartsData.topSizes.map(s => s.count);
    charts.size = new Chart(document.getElementById("sizeChart"), {{
        type: "bar",
        data: {{
            labels: sizeLabels,
            datasets: [{{
                label: "Количество запросов",
                data: sizeValues,
                backgroundColor: "rgba(16, 185, 129, 0.75)",
                borderRadius: 6
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ ticks: {{ color: "#94a3b8", font: {{ size: 11 }} }}, grid: {{ display: false }} }},
                y: {{ ticks: {{ color: "#94a3b8" }}, grid: {{ color: "rgba(255,255,255,0.05)" }} }}
            }}
        }}
    }});

    // 6. Темы
    const intentLabels = chartsData.intents.map(i => i.intent);
    const intentValues = chartsData.intents.map(i => i.count);
    charts.intent = new Chart(document.getElementById("intentChart"), {{
        type: "bar",
        data: {{
            labels: intentLabels,
            datasets: [{{
                label: "Запросов",
                data: intentValues,
                backgroundColor: "rgba(59, 130, 246, 0.75)",
                borderRadius: 6
            }}]
        }},
        options: {{
            indexAxis: "y",
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ ticks: {{ color: "#94a3b8" }}, grid: {{ color: "rgba(255,255,255,0.05)" }} }},
                y: {{ ticks: {{ color: "#94a3b8", font: {{ size: 11 }} }}, grid: {{ display: false }} }}
            }}
        }}
    }});

    // 7. Бренды
    const brandLabels = chartsData.topBrands.map(b => b.brand);
    const brandValues = chartsData.topBrands.map(b => b.count);
    charts.brand = new Chart(document.getElementById("brandChart"), {{
        type: "bar",
        data: {{
            labels: brandLabels,
            datasets: [{{
                label: "Упоминания",
                data: brandValues,
                backgroundColor: "rgba(245, 158, 11, 0.75)",
                borderRadius: 6
            }}]
        }},
        options: {{
            indexAxis: "y",
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ ticks: {{ color: "#94a3b8" }}, grid: {{ color: "rgba(255,255,255,0.05)" }} }},
                y: {{ ticks: {{ color: "#94a3b8", font: {{ size: 11 }} }}, grid: {{ display: false }} }}
            }}
        }}
    }});
}}

function populateFilters(chartsData) {{
    const sourceSelect = document.getElementById("sourceFilter");
    chartsData.sources.forEach(s => {{
        const opt = document.createElement("option");
        opt.value = s.source;
        opt.innerText = `🌐 ${{s.source}} (${{s.count}})`;
        sourceSelect.appendChild(opt);
    }});

    const citySelect = document.getElementById("cityFilter");
    chartsData.cities.forEach(c => {{
        const opt = document.createElement("option");
        opt.value = c.city;
        opt.innerText = `${{c.city}} (${{c.count}})`;
        citySelect.appendChild(opt);
    }});

    const catSelect = document.getElementById("categoryFilter");
    chartsData.categories.forEach(cat => {{
        const opt = document.createElement("option");
        opt.value = cat.category;
        opt.innerText = `${{cat.category}} (${{cat.count}})`;
        catSelect.appendChild(opt);
    }});

    const opSelect = document.getElementById("operatorFilter");
    chartsData.operators.forEach(op => {{
        const opt = document.createElement("option");
        opt.value = op.name;
        opt.innerText = `${{op.name}} (${{op.count}})`;
        opSelect.appendChild(opt);
    }});
}}

function populateDatePresets(dialogs) {{
    const monthSet = new Set();
    dialogs.forEach(d => {{
        if (d.createdAt && d.createdAt.length >= 7) {{
            monthSet.add(d.createdAt.slice(0, 7));
        }}
    }});
    const sortedMonths = Array.from(monthSet).sort().reverse();
    const presetSelect = document.getElementById("datePresetFilter");
    const monthNames = {{
        "01": "Январь", "02": "Февраль", "03": "Март", "04": "Апрель",
        "05": "Май", "06": "Июнь", "07": "Июль", "08": "Август",
        "09": "Сентябрь", "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь"
    }};
    sortedMonths.forEach(m => {{
        const [yyyy, mm] = m.split("-");
        const name = `${{monthNames[mm] || mm}} ${{yyyy}}`;
        const count = dialogs.filter(d => d.createdAt && d.createdAt.startsWith(m)).length;
        const opt = document.createElement("option");
        opt.value = m;
        opt.innerText = `📅 ${{name}} (${{count}})`;
        presetSelect.appendChild(opt);
    }});
}}

function computeFilteredAggregates(dialogs) {{
    const total = dialogs.length;
    if (total === 0) {{
        return {{
            summary: {{
                totalDialogs: 0, uniqueClients: 0,
                salesCount: 0, salesPct: 0,
                serviceCount: 0, servicePct: 0,
                conversionRate: 0, totalConversions: 0,
                botOnlyCount: 0, botOnlyPct: 0,
                operatorEscalatedCount: 0, operatorEscalatedPct: 0,
                withCityCount: 0
            }},
            sources: [], outcomes: [], cities: [], intents: [], topSizes: [], topBrands: [],
            managerPerformance: []
        }};
    }}

    const sourceCount = {{}};
    const outcomeCount = {{}};
    const cityCount = {{}};
    const intentCount = {{}};
    const sizeCount = {{}};
    const brandCount = {{}};
    const clients = new Set();
    const managerBreakdown = {{}};

    let botOnly = 0;
    let opEscalated = 0;
    let withCity = 0;
    let sales = 0;
    let services = 0;

    for (let i = 0; i < total; i++) {{
        const d = dialogs[i];
        clients.add(d.client);
        
        if (d.isSale) sales++;
        if (d.isService) services++;

        if (d.operatorPresent) {{
            opEscalated++;
            const op = d.operator;
            if (!managerBreakdown[op]) managerBreakdown[op] = {{ total: 0, sales: 0, service: 0, consult: 0 }};
            managerBreakdown[op].total++;
            if (d.isSale) managerBreakdown[op].sales++;
            else if (d.isService) managerBreakdown[op].service++;
            else managerBreakdown[op].consult++;
        }} else {{
            botOnly++;
        }}

        if (d.city && d.city !== "Не определен") withCity++;
        cityCount[d.city] = (cityCount[d.city] || 0) + 1;
        sourceCount[d.source] = (sourceCount[d.source] || 0) + 1;
        outcomeCount[d.outcome] = (outcomeCount[d.outcome] || 0) + 1;

        if (d.intents) {{
            d.intents.forEach(it => {{ intentCount[it] = (intentCount[it] || 0) + 1; }});
        }}
        if (d.tireSizes) {{
            d.tireSizes.forEach(s => {{ sizeCount[s] = (sizeCount[s] || 0) + 1; }});
        }}
        if (d.brands) {{
            d.brands.forEach(b => {{ brandCount[b] = (brandCount[b] || 0) + 1; }});
        }}
    }}

    const namedCounts = {{}};
    for (let i = 0; i < total; i++) {{
        const cl = dialogs[i].client;
        if (cl && cl !== "Клиент" && cl !== "Гость" && cl !== "—") {{
            namedCounts[cl] = (namedCounts[cl] || 0) + 1;
        }}
    }}
    const repeatCount = Object.values(namedCounts).filter(v => v > 1).length;
    const repeatPct = clients.size ? Math.round((repeatCount / clients.size) * 1000) / 10 : 0;

    const sortEntries = (obj, limit = 12) => Object.entries(obj).sort((a,b) => b[1] - a[1]).slice(0, limit);

    const managerPerf = Object.entries(managerBreakdown).map(([k, v]) => ({{
        name: k, total: v.total, sales: v.sales, service: v.service, consult: v.consult
    }})).sort((a,b) => b.total - a.total);

    const totalConversions = sales + services;

    return {{
        summary: {{
            totalDialogs: total,
            uniqueClients: clients.size,
            repeatClientsCount: repeatCount,
            repeatClientsPct: repeatPct,
            salesCount: sales,
            salesPct: Math.round((sales / total) * 1000) / 10,
            serviceCount: services,
            servicePct: Math.round((services / total) * 1000) / 10,
            totalConversions: totalConversions,
            conversionRate: Math.round((totalConversions / total) * 1000) / 10,
            botOnlyCount: botOnly,
            botOnlyPct: Math.round((botOnly / total) * 1000) / 10,
            operatorEscalatedCount: opEscalated,
            operatorEscalatedPct: Math.round((opEscalated / total) * 1000) / 10,
            withCityCount: withCity
        }},
        sources: sortEntries(sourceCount, 8).map(([k, v]) => ({{ source: k, count: v }})),
        outcomes: sortEntries(outcomeCount, 8).map(([k, v]) => ({{ outcome: k, count: v }})),
        cities: sortEntries(cityCount, 10).map(([k, v]) => ({{ city: k, count: v }})),
        intents: sortEntries(intentCount, 6).map(([k, v]) => ({{ intent: k, count: v }})),
        topSizes: sortEntries(sizeCount, 12).map(([k, v]) => ({{ size: k, count: v }})),
        topBrands: sortEntries(brandCount, 10).map(([k, v]) => ({{ brand: k, count: v }})),
        managerPerformance: managerPerf
    }};
}}

function updateChartsData(agg) {{
    if (charts.source) {{
        charts.source.data.labels = agg.sources.map(s => s.source);
        charts.source.data.datasets[0].data = agg.sources.map(s => s.count);
        charts.source.update();
    }}
    if (charts.outcome) {{
        charts.outcome.data.labels = agg.outcomes.map(o => o.outcome);
        charts.outcome.data.datasets[0].data = agg.outcomes.map(o => o.count);
        charts.outcome.update();
    }}
    if (charts.city) {{
        charts.city.data.labels = agg.cities.map(c => c.city);
        charts.city.data.datasets[0].data = agg.cities.map(c => c.count);
        charts.city.update();
    }}
    if (charts.manager) {{
        const mgrNames = agg.managerPerformance.map(m => m.name.split(" ")[0] || m.name);
        charts.manager.data.labels = mgrNames;
        charts.manager.data.datasets[0].data = agg.managerPerformance.map(m => m.sales);
        charts.manager.data.datasets[1].data = agg.managerPerformance.map(m => m.service);
        charts.manager.data.datasets[2].data = agg.managerPerformance.map(m => m.consult);
        charts.manager.update();
    }}
    if (charts.size) {{
        charts.size.data.labels = agg.topSizes.map(s => s.size);
        charts.size.data.datasets[0].data = agg.topSizes.map(s => s.count);
        charts.size.update();
    }}
    if (charts.intent) {{
        charts.intent.data.labels = agg.intents.map(i => i.intent);
        charts.intent.data.datasets[0].data = agg.intents.map(i => i.count);
        charts.intent.update();
    }}
    if (charts.brand) {{
        charts.brand.data.labels = agg.topBrands.map(b => b.brand);
        charts.brand.data.datasets[0].data = agg.topBrands.map(b => b.count);
        charts.brand.update();
    }}
}}

let currentPage = 1;
const pageSize = 50;
let currentFilteredDialogs = [];

function renderTable(dialogs) {{
    currentFilteredDialogs = dialogs;
    currentPage = 1;
    document.getElementById("counterLabel").innerText = `Найдено: ${{dialogs.length}}`;
    renderCurrentPage();
}}

function renderCurrentPage() {{
    const tbody = document.getElementById("dialogsTableBody");
    const total = currentFilteredDialogs.length;

    if (total === 0) {{
        tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; padding: 40px; color: var(--text-muted);">Диалоги по выбранным фильтрам не найдены.</td></tr>`;
        document.getElementById("pageInfo").innerText = "Нет записей";
        document.getElementById("pageNumbers").innerHTML = "";
        document.getElementById("prevPageBtn").disabled = true;
        document.getElementById("nextPageBtn").disabled = true;
        return;
    }}

    const totalPages = Math.ceil(total / pageSize);
    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;

    const startIdx = (currentPage - 1) * pageSize;
    const endIdx = Math.min(startIdx + pageSize, total);
    const pageItems = currentFilteredDialogs.slice(startIdx, endIdx);

    tbody.innerHTML = pageItems.map((d, idx) => `
        <tr onclick="openChatModal('${{d.sid}}')">
            <td>
                <div style="font-weight:700; color:#38bdf8;">#${{d.sid}}</div>
                <div style="font-size:11px; color:var(--text-muted);">${{d.createdAt || "—"}}</div>
            </td>
            <td>
                <div style="font-weight:600;">${{d.client || "Гость"}}</div>
                <div style="font-size:11px; color:var(--text-muted);">${{d.channel || "WhatsApp"}}</div>
                ${{d.clientTotalSessions > 1 ? `<div style="margin-top:3px;"><span class="badge badge-repeat" title="Всего обращений в истории: ${{d.clientTotalSessions}}">🔁 ${{d.clientTotalSessions}} визита</span></div>` : ''}}
            </td>
            <td>${{getSourceBadgeHtml(d.source)}}</td>
            <td><span class="badge badge-city">${{d.city}}</span></td>
            <td><span class="badge" style="background:rgba(255,255,255,0.08);">${{d.category}}</span></td>
            <td>${{d.primarySize !== "—" ? `<span class="badge badge-size">${{d.primarySize}}</span>` : `<span style="color:var(--text-muted);">—</span>`}}</td>
            <td>
                ${{d.primaryBrand !== "—" ? `<span class="badge badge-brand">${{d.primaryBrand}}</span>` : ""}}
                ${{d.carModel !== "—" ? `<div style="font-size:11px; color:#cbd5e1; margin-top:2px;">🚗 ${{d.carModel}}</div>` : ""}}
                ${{d.primaryBrand === "—" && d.carModel === "—" ? `<span style="color:var(--text-muted);">—</span>` : ""}}
            </td>
            <td>
                ${{d.operatorPresent ? `<span class="badge badge-op">👨‍💼 ${{d.operator}}</span>` : `<span class="badge badge-bot">🤖 AI-бот</span>`}}
            </td>
            <td>${{getOutcomeBadgeHtml(d.outcome, d.isSale, d.isService)}}</td>
            <td><span style="font-size:12px; color:var(--text-muted);">${{d.primaryIntent}}</span></td>
            <td style="font-weight:700; text-align:center;">${{d.messagesCount}}</td>
        </tr>
    `).join("");

    document.getElementById("pageInfo").innerText = `Показано ${{startIdx + 1}}–${{endIdx}} из ${{total}}`;
    document.getElementById("prevPageBtn").disabled = (currentPage === 1);
    document.getElementById("nextPageBtn").disabled = (currentPage === totalPages);

    // Пагинация номеров страниц
    let pagesToShow = [];
    if (totalPages <= 7) {{
        pagesToShow = Array.from({{length: totalPages}}, (_, i) => i + 1);
    }} else {{
        if (currentPage <= 4) {{
            pagesToShow = [1, 2, 3, 4, 5, "...", totalPages];
        }} else if (currentPage >= totalPages - 3) {{
            pagesToShow = [1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
        }} else {{
            pagesToShow = [1, "...", currentPage - 1, currentPage, currentPage + 1, "...", totalPages];
        }}
    }}

    document.getElementById("pageNumbers").innerHTML = pagesToShow.map(p => {{
        if (p === "...") return `<span style="color:var(--text-muted); padding:4px 6px;">...</span>`;
        return `<button class="page-num ${{p === currentPage ? 'active' : ''}}" onclick="goToPage(${{p}})">${{p}}</button>`;
    }}).join("");
}}

function changePage(delta) {{
    currentPage += delta;
    renderCurrentPage();
    const tableEl = document.querySelector('.table-card');
    if (tableEl) window.scrollTo({{ top: tableEl.offsetTop - 80, behavior: 'smooth' }});
}}

function goToPage(p) {{
    currentPage = p;
    renderCurrentPage();
    const tableEl = document.querySelector('.table-card');
    if (tableEl) window.scrollTo({{ top: tableEl.offsetTop - 80, behavior: 'smooth' }});
}}

function setupEventListeners() {{
    const search = document.getElementById("searchInput");
    const datePreset = document.getElementById("datePresetFilter");
    const dateFrom = document.getElementById("dateFrom");
    const dateTo = document.getElementById("dateTo");
    const source = document.getElementById("sourceFilter");
    const outcome = document.getElementById("outcomeFilter");
    const city = document.getElementById("cityFilter");
    const cat = document.getElementById("categoryFilter");
    const op = document.getElementById("operatorFilter");
    const resetBtn = document.getElementById("resetFiltersBtn");

    function applyFilter() {{
        const q = search.value.toLowerCase().trim();
        const selSource = source.value;
        const selOutcome = outcome.value;
        const selCity = city.value;
        const selCat = cat.value;
        const selOp = op.value;
        const dFrom = dateFrom.value;
        const dTo = dateTo.value;

        const filtered = allDialogs.filter(d => {{
            const dDate = d.createdAt ? d.createdAt.slice(0, 10) : "";
            if (dFrom && (!dDate || dDate < dFrom)) return false;
            if (dTo && (!dDate || dDate > dTo)) return false;
            
            if (selSource && d.source !== selSource) return false;
            
            if (selOutcome) {{
                if (selOutcome === "sale" && !d.isSale) return false;
                if (selOutcome === "service" && !d.isService) return false;
                if (selOutcome === "consultation" && d.outcomeType !== "consultation") return false;
                if (selOutcome === "in_progress" && d.outcomeType !== "in_progress") return false;
                if (selOutcome === "lost" && d.outcomeType !== "lost") return false;
                if (selOutcome === "bot" && d.operatorPresent) return false;
            }}

            if (selCity && d.city !== selCity) return false;
            if (selCat && d.category !== selCat) return false;
            if (selOp && d.operator !== selOp) return false;

            if (q) {{
                const matches = (
                    d.client.toLowerCase().includes(q) ||
                    d.sid.includes(q) ||
                    (d.source && d.source.toLowerCase().includes(q)) ||
                    (d.outcome && d.outcome.toLowerCase().includes(q)) ||
                    d.rawTextSnippet.toLowerCase().includes(q) ||
                    d.city.toLowerCase().includes(q) ||
                    d.tireSizes.some(s => s.toLowerCase().includes(q)) ||
                    d.brands.some(b => b.toLowerCase().includes(q)) ||
                    d.carModel.toLowerCase().includes(q)
                );
                if (!matches) return false;
            }}

            return true;
        }});

        const agg = computeFilteredAggregates(filtered);
        renderKPIs(agg.summary);
        updateChartsData(agg);
        renderTable(filtered);
    }}

    datePreset.addEventListener("change", () => {{
        const val = datePreset.value;
        if (!val) {{
            dateFrom.value = "";
            dateTo.value = "";
        }} else {{
            const [year, month] = val.split("-").map(Number);
            const firstDay = `${{val}}-01`;
            const lastDayDate = new Date(year, month, 0).getDate();
            const lastDay = `${{val}}-${{String(lastDayDate).padStart(2, '0')}}`;
            dateFrom.value = firstDay;
            dateTo.value = lastDay;
        }}
        applyFilter();
    }});

    dateFrom.addEventListener("change", () => {{ datePreset.value = ""; applyFilter(); }});
    dateTo.addEventListener("change", () => {{ datePreset.value = ""; applyFilter(); }});
    search.addEventListener("input", applyFilter);
    source.addEventListener("change", applyFilter);
    outcome.addEventListener("change", applyFilter);
    city.addEventListener("change", applyFilter);
    cat.addEventListener("change", applyFilter);
    op.addEventListener("change", applyFilter);

    resetBtn.addEventListener("click", () => {{
        search.value = "";
        datePreset.value = "";
        dateFrom.value = "";
        dateTo.value = "";
        source.value = "";
        outcome.value = "";
        city.value = "";
        cat.value = "";
        op.value = "";
        applyFilter();
    }});
}}

let currentModalSessions = [];
let currentActiveSessionSid = "all";

function openChatModal(sid, targetSid = "all") {{
    const dialog = allDialogs.find(d => d.sid === sid);
    if (!dialog) return;

    const isAnonymous = !dialog.client || ["Клиент", "Гость", "", "—"].includes(dialog.client);
    currentModalSessions = isAnonymous
        ? [dialog]
        : allDialogs.filter(d => d.client === dialog.client).sort((a,b) => (a.createdAt || "").localeCompare(b.createdAt || ""));
    
    currentActiveSessionSid = targetSid;

    document.getElementById("modalClientName").innerText = dialog.client || "Клиент";
    document.getElementById("modalAvatar").innerText = (dialog.client || "💬").charAt(0).toUpperCase() || "💬";

    const navBar = document.getElementById("modalSessionNav");
    if (currentModalSessions.length > 1) {{
        navBar.style.display = "flex";
        let navHtml = `
            <button class="session-tab-btn ${{currentActiveSessionSid === 'all' ? 'active' : ''}}" onclick="switchModalSession('all')">
                🌐 Вся история (${{currentModalSessions.length}})
            </button>
        `;
        currentModalSessions.forEach((s, idx) => {{
            const dateStr = s.createdAt ? s.createdAt.slice(5, 16) : `#${{s.sid}}`;
            navHtml += `
                <button class="session-tab-btn ${{currentActiveSessionSid === s.sid ? 'active' : ''}}" onclick="switchModalSession('${{s.sid}}')">
                    #${{idx + 1}} (${{dateStr}})
                </button>
            `;
        }});
        navBar.innerHTML = navHtml;
    }} else {{
        navBar.style.display = "none";
        navBar.innerHTML = "";
    }}

    renderModalBody(dialog);
    document.getElementById("chatModal").style.display = "flex";
}}

function switchModalSession(targetSid) {{
    currentActiveSessionSid = targetSid;
    const navBar = document.getElementById("modalSessionNav");
    const btns = navBar.querySelectorAll(".session-tab-btn");
    btns.forEach(btn => {{
        if (targetSid === 'all' && btn.innerText.includes("Вся история")) {{
            btn.classList.add("active");
        }} else if (btn.getAttribute("onclick") && btn.getAttribute("onclick").includes(`'${{targetSid}}'`)) {{
            btn.classList.add("active");
        }} else {{
            btn.classList.remove("active");
        }}
    }});

    const activeDialog = currentActiveSessionSid === 'all'
        ? currentModalSessions[currentModalSessions.length - 1]
        : (currentModalSessions.find(s => s.sid === currentActiveSessionSid) || currentModalSessions[0]);
    renderModalBody(activeDialog);
}}

function renderModalBody(activeDialog) {{
    const sessionsToRender = currentActiveSessionSid === 'all'
        ? currentModalSessions
        : currentModalSessions.filter(s => s.sid === currentActiveSessionSid);

    // Sub-info header
    if (currentModalSessions.length > 1 && currentActiveSessionSid === 'all') {{
        document.getElementById("modalSubInfo").innerHTML = `
            <span>📍 ${{activeDialog.city}}</span>
            <span>•</span>
            <span class="badge badge-repeat">🔁 Всего обращений: ${{currentModalSessions.length}}</span>
            <span>•</span>
            <span>Последнее: ${{activeDialog.createdAt || '—'}}</span>
        `;
        document.getElementById("modalMetaLeft").innerText = `История клиента: ${{currentModalSessions.length}} сессий с ${{currentModalSessions[0].createdAt ? currentModalSessions[0].createdAt.slice(0,10) : '—'}} по ${{activeDialog.createdAt ? activeDialog.createdAt.slice(0,10) : '—'}}`;
        document.getElementById("modalMetaRight").innerText = `Всего сообщений: ${{currentModalSessions.reduce((acc, s) => acc + (s.messagesCount || 0), 0)}}`;
    }} else {{
        document.getElementById("modalSubInfo").innerHTML = `
            <span>📍 ${{activeDialog.city}}</span>
            <span>•</span>
            <span>${{getSourceBadgeHtml(activeDialog.source)}}</span>
            <span>•</span>
            <span>${{getOutcomeBadgeHtml(activeDialog.outcome, activeDialog.isSale, activeDialog.isService)}}</span>
            ${{activeDialog.primarySize && activeDialog.primarySize !== '—' ? `<span>• 🛞 ${{activeDialog.primarySize}}</span>` : ''}}
        `;
        document.getElementById("modalMetaLeft").innerText = `SID: #${{activeDialog.sid}} | Начало: ${{activeDialog.createdAt}} | Длительность: ${{activeDialog.durationMin}} мин`;
        document.getElementById("modalMetaRight").innerText = activeDialog.operatorPresent ? `👨‍💼 Менеджер: ${{activeDialog.operator}}` : `🤖 AI-бот`;
    }}

    const chatBody = document.getElementById("chatBody");
    let fullHtml = "";

    sessionsToRender.forEach((sess, sessIdx) => {{
        const globalIdx = currentModalSessions.findIndex(s => s.sid === sess.sid);
        if (currentModalSessions.length > 1) {{
            fullHtml += `
                <div class="session-divider">
                    <div class="session-divider-badge">
                        <span>📅 <strong>Обращение #${{globalIdx + 1}}</strong> (${{sess.createdAt || "—"}})</span>
                        <span>•</span>
                        <span>SID #${{sess.sid}}</span>
                        <span>•</span>
                        <span>${{getSourceBadgeHtml(sess.source)}}</span>
                        <span>•</span>
                        <span>${{getOutcomeBadgeHtml(sess.outcome, sess.isSale, sess.isService)}}</span>
                        ${{sess.operatorPresent ? `<span>• 👨‍💼 ${{sess.operator}}</span>` : `<span>• 🤖 Бот</span>`}}
                        ${{sess.primarySize && sess.primarySize !== '—' ? `<span>• 🛞 ${{sess.primarySize}}</span>` : ''}}
                    </div>
                </div>
            `;
        }}

        if (sess.messages && sess.messages.length > 0) {{
            fullHtml += sess.messages.map(m => {{
                let roleClass = "bubble-client";
                let roleLabel = "Клиент";
                if (m.role === "bot") {{ roleClass = "bubble-bot"; roleLabel = "AI Бот"; }}
                else if (m.role === "operator") {{ roleClass = "bubble-operator"; roleLabel = "Оператор (" + m.sender + ")"; }}

                return `
                    <div class="chat-bubble ${{roleClass}}">
                        <div class="bubble-sender">${{roleLabel}}</div>
                        <div style="white-space: pre-wrap;">${{m.text}}</div>
                        <div class="bubble-time">${{m.time}}</div>
                    </div>
                `;
            }}).join("");
        }} else {{
            fullHtml += `<div style="text-align:center; padding:30px; color:var(--text-muted); font-size:13px;">${{sess.rawTextSnippet || "Текст переписки отсутствует."}}</div>`;
        }}
    }});

    chatBody.innerHTML = fullHtml || `<div style="text-align:center; padding:40px; color:var(--text-muted);">Переписка пуста.</div>`;
    chatBody.scrollTop = chatBody.scrollHeight;
}}

function closeChatModal() {{
    document.getElementById("chatModal").style.display = "none";
}}

window.addEventListener("DOMContentLoaded", initDashboard);
</script>
</body>
</html>
"""
    with open(DASHBOARD_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)
    logging.info(f"[+] Дашборд сгенерирован: {DASHBOARD_HTML_PATH}")

def run_analysis():
    """Основной пайплайн анализа всех CSV файлов в папке dialog/."""
    os.makedirs(DIALOG_DIR, exist_ok=True)
    csv_files = glob.glob(os.path.join(DIALOG_DIR, "*.csv"))

    if not csv_files:
        logging.warning(f"[!] В папке {DIALOG_DIR} не найдено CSV-файлов выгрузки.")
        return

    logging.info(f"Найдено файлов выгрузок: {len(csv_files)}")
    
    # 1. Загрузка ранее сохраненных диалогов для дедупликации
    existing_dialogs = []
    existing_sids = set()
def run_analysis(rebuild=False):
    """Основной пайплайн анализа всех CSV файлов в папке dialog/."""
    os.makedirs(DIALOG_DIR, exist_ok=True)
    csv_files = glob.glob(os.path.join(DIALOG_DIR, "*.csv"))

    if not csv_files:
        logging.warning(f"[!] В папке {DIALOG_DIR} не найдено CSV-файлов выгрузки.")
        return

    logging.info(f"Найдено файлов выгрузок: {len(csv_files)}")
    
    # 1. Загрузка ранее сохраненных диалогов для инкрементальной обработки
    existing_dialogs = []
    existing_sids = set()
    if not rebuild and os.path.exists(OUTPUT_JSON_PATH):
        try:
            with open(OUTPUT_JSON_PATH, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                existing_dialogs = old_data.get("dialogs", [])
                # Автоматическое дообогащение полей при обновлении схемы
                for d in existing_dialogs:
                    if "source" not in d or "isSale" not in d:
                        raw_text = d.get("rawTextSnippet", "")
                        msgs = d.get("messages", [])
                        d["source"] = extract_source(raw_text, msgs, {})
                        outcome_label, outcome_type = extract_detailed_outcome(raw_text, msgs, {}, d.get("operatorPresent", False))
                        d["outcome"] = outcome_label
                        d["outcomeType"] = outcome_type
                        d["isSale"] = outcome_type == "sale"
                        d["isService"] = outcome_type == "service"
                existing_sids = set(d["sid"] for d in existing_dialogs if "sid" in d)
                logging.info(f"Загружено {len(existing_dialogs)} ранее обработанных диалогов из кэша.")
        except Exception as e:
            logging.warning(f"Не удалось прочитать существующий {OUTPUT_JSON_PATH}: {e}")

    # 2. Обработка всех CSV файлов
    new_dialogs = []
    for csv_file in csv_files:
        parsed = process_csv_file(csv_file, existing_sids)
        new_dialogs.extend(parsed)

    all_dialogs = existing_dialogs + new_dialogs
    # Сортировка по дате создания
    all_dialogs.sort(key=lambda d: d.get("createdAt", ""), reverse=True)

    # 3. Расчет сводной аналитики
    aggregates = compute_aggregates(all_dialogs)

    export_payload = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totalFilesProcessed": len(csv_files),
        "totalDialogs": len(all_dialogs),
        "aggregates": aggregates,
        "dialogs": all_dialogs
    }

    # 4. Сохранение JSON
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(export_payload, f, ensure_ascii=False, indent=2)
    logging.info(f"[+] Данные сохранены: {OUTPUT_JSON_PATH} (Всего диалогов: {len(all_dialogs)})")

    # 5. Генерация HTML Дашборда
    generate_dashboard_html(export_payload)

def watch_directory(interval=5):
    """Мониторит папку dialog/ и автоматически перезапускает анализ при добавлении/изменении CSV."""
    logging.info(f"[*] Запущен режим наблюдения за папкой: {DIALOG_DIR} (интервал {interval} сек)")
    known_mtimes = {}

    while True:
        try:
            csv_files = glob.glob(os.path.join(DIALOG_DIR, "*.csv"))
            current_mtimes = {f: os.path.getmtime(f) for f in csv_files}

            if current_mtimes != known_mtimes:
                logging.info("[*] Обнаружены изменения или новые файлы в папке dialog. Запуск анализа...")
                run_analysis()
                known_mtimes = current_mtimes

            time.sleep(interval)
        except KeyboardInterrupt:
            logging.info("Остановка наблюдения.")
            break
        except Exception as e:
            logging.error(f"Ошибка в цикле наблюдения: {e}")
            time.sleep(interval)

def main():
    parser = argparse.ArgumentParser(description="AIKOS CRM — Анализ диалогов Bitrix24 WhatsApp")
    parser.add_argument("--watch", action="store_true", help="Запустить в режиме непрерывного мониторинга новых файлов")
    parser.add_argument("--interval", type=int, default=5, help="Интервал проверки в секундах для режима --watch")
    parser.add_argument("--rebuild", action="store_true", help="Полная переобработка всех файлов с нуля без кэша")
    args = parser.parse_args()

    run_analysis(rebuild=args.rebuild)

    if args.watch:
        watch_directory(args.interval)

if __name__ == "__main__":
    main()
