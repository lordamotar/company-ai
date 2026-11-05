# -*- coding: utf-8 -*-
"""
Оценка работы менеджера в текстовом диалоге (Bitrix24, открытые линии) — по правилам, без ИИ и без сети.

Для каждого диалога, где отвечал менеджер, считается:
  • скорость первого ответа после передачи от ИИ-ассистента и средняя скорость ответов (в рабочих минутах);
  • остался ли клиент без ответа;
  • чек-лист из 6 пунктов по тексту ответов менеджера (приветствие, уточнение потребности, предложение товара,
    цена, призыв к покупке/записи, вежливое завершение) и итоговая оценка 0–100.
Если ИИ-ассистент передал диалог менеджеру, а менеджер так и не написал, диалог помечается отдельно.
"""

import re
from datetime import datetime, timedelta

# Рабочее время менеджеров (местное время в тексте переписки). Ожидание ночью не считается задержкой.
WORK_START_HOUR = 9
WORK_END_HOUR = 20
SLOW_FIRST_RESPONSE_MIN = 30   # дольше — «долгий первый ответ»

CHECKLIST = [
    ("greeting", "Приветствие"),
    ("needs", "Уточнил потребность"),
    ("offer", "Предложил товар/услугу"),
    ("price", "Назвал цену"),
    ("cta", "Призыв к покупке/записи"),
    ("farewell", "Вежливое завершение"),
]

_I = re.IGNORECASE
HANDOFF_RE = re.compile(r"переда(ю|л|м)[^.!?\n]{0,40}менеджер|дождитесь ответа менеджера|менеджер подключится|"
                        r"с вами свяжутся|перевожу на менеджера", _I)
GREETING_RE = re.compile(r"здра(в)?ств|добр(ый|ое|ого)\s|доброго|сәлем|салем|привет", _I)
NEEDS_RE = re.compile(r"город|размер|радиус|сезон|авто|машин|марк[аиу]|модел|бюджет|параметр|интересу|"
                      r"как(ой|ая|ие|ую|ого)|сколько|подскаж|диаметр|шипован|летн|зимн|колич", _I)
SIZE_RE = re.compile(r"\b\d{3}/\d{2}\s?Z?R\s?\d{2}|\b\d{2}[.,]?\d?-\d{2}\b|\bR\s?\d{2}\b", _I)
OFFER_RE = re.compile(r"aikos\.kz/catalog|могу предложить|предлага|есть в наличии|в наличии есть|"
                      r"имеется|есть вариант|подобрал|подобрать", _I)
CURRENCY_RE = re.compile(r"\d[\d\s ]{2,}\s*(₸|тг\b|тенге|т\.)|\d+\s*тыс|цен[аы][^.\n]{0,25}\d{3}|стоит[^.\n]{0,15}\d{3}", _I)
LINE_PRICE_RE = re.compile(r"\b\d{1,3}(?:[  ]\d{3})+\s*$|\b\d{4,7}\s*$", re.MULTILINE)
CTA_RE = re.compile(r"оформ|заказ|брон|запис|оплат|сч[её]т\b|kaspi|каспи|доставк|самовывоз|приезжайте|"
                    r"ждём вас|ждем вас|резерв|отлож|рассрочк|договор", _I)
FAREWELL_RE = re.compile(r"спасибо|благодар|рахмет|хорошего дня|всего доброго|обращайтесь|до свидания", _I)
# Короткие реплики клиента, на которые ответ не обязателен: благодарность, согласие, «уже нашёл»
CLOSING_CLIENT_RE = re.compile(r"спасибо|спс|рахмет|благодар|\bок\b|ok|хорошо|понял|ясно|норм|нашел|нашёл|"
                               r"не надо|не нужно|до свидания|\bда\b|ага|👍|🙏", _I)
# Служебные сообщения интеграции WhatsApp записаны в выгрузке от имени клиента
SYSTEM_RE = re.compile(r"=== SYSTEM|оцените качество обслуживания|Просим прощения что не смогли", _I)


def needs_reply(text):
    """Пустые сообщения (вложения), реакции [like] и короткие «спасибо/понял» ответа не требуют."""
    text = (text or "").strip()
    if not text or re.fullmatch(r"\[\w+\]", text):
        return False
    return not (len(text) <= 40 and CLOSING_CLIENT_RE.search(text))


# Учётная запись Bitrix24 → кто на самом деле отвечает с неё. Ключ — «Имя Фамилия» учётной записи.
ACCOUNT_TO_MANAGER = {
    "Айнур Усманова": "Тауфик",
}


def short_name(full_name):
    """«Айнур Жолдасовна Усманова» → «Айнур Усманова», чтобы совпадало с колонкой «Сотрудник»."""
    parts = (full_name or "").split()
    return f"{parts[0]} {parts[-1]}" if len(parts) >= 2 else (full_name or "").strip()


def manager_name(account_name):
    """Имя менеджера для отчёта: короткое имя учётной записи с учётом ACCOUNT_TO_MANAGER."""
    name = short_name(account_name)
    return ACCOUNT_TO_MANAGER.get(name, name)


def _parse_time(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def working_minutes(start, end):
    """Минуты между start и end, попадающие в рабочие часы."""
    if not start or not end or end <= start:
        return 0.0
    total, day = 0.0, start.replace(hour=0, minute=0, second=0)
    while day <= end:
        w_start = max(start, day.replace(hour=WORK_START_HOUR))
        w_end = min(end, day.replace(hour=WORK_END_HOUR))
        if w_end > w_start:
            total += (w_end - w_start).total_seconds() / 60
        day += timedelta(days=1)
    return round(total, 1)


def _checklist(operator_texts):
    joined = "\n".join(operator_texts)
    return {
        "greeting": bool(GREETING_RE.search(joined)),
        "needs": any("?" in t and NEEDS_RE.search(t) and "чем могу помочь" not in t.lower() for t in operator_texts),
        "offer": bool(SIZE_RE.search(joined) or OFFER_RE.search(joined)),
        "price": bool(CURRENCY_RE.search(joined) or any(SIZE_RE.search(l) and LINE_PRICE_RE.search(l)
                                                        for l in joined.splitlines())),
        "cta": bool(CTA_RE.search(joined)),
        "farewell": bool(FAREWELL_RE.search(joined)),
    }


def analyze_manager(messages, assigned_employee=""):
    """
    messages — список {time, sender, role: client|bot|operator, text}.
    Возвращает словарь оценки или None, если менеджер в диалоге не участвовал и диалог ему не передавался.
    """
    messages =[m for m in messages if not SYSTEM_RE.search(m["text"] or "")]

    ops = [m for m in messages if m["role"] == "operator"]
    handoff = next((m for m in messages if m["role"] == "bot" and HANDOFF_RE.search(m["text"])), None)

    if not ops:
        if not handoff:
            return None
        name = assigned_employee if assigned_employee and not assigned_employee.isdigit() else "Не назначен"
        return {"name": manager_name(name), "noReply": True, "handoffAt": handoff["time"], "score": 0,
                "flags": ["Передан менеджеру, ответа нет"]}

    first_op_index = messages.index(ops[0])
    first_op_time = _parse_time(ops[0]["time"])
    # Отсчёт ожидания: фраза бота «передаю менеджеру» или вопрос клиента, оставшийся без ответа бота.
    # Если бот уже ответил и менеджер написал позже сам («Получилось выбрать?»), это не задержка.
    start = None
    if handoff and messages.index(handoff) < first_op_index:
        start = _parse_time(handoff["time"])
    elif first_op_index > 0 and messages[first_op_index - 1]["role"] == "client":
        waiting = first_op_index - 1
        while waiting > 0 and messages[waiting - 1]["role"] == "client":
            waiting -= 1
        start = _parse_time(messages[waiting]["time"])
    first_response = working_minutes(start, first_op_time) if start else None

    # Ответы менеджера на последующие сообщения клиента; реплики бота ожидание не прерывают
    waits, pending = [], None
    for m in messages[first_op_index + 1:]:
        if m["role"] == "client" and pending is None and needs_reply(m["text"]):
            pending = _parse_time(m["time"])
        elif m["role"] == "operator" and pending is not None:
            waits.append(working_minutes(pending, _parse_time(m["time"])))
            pending = None
    unanswered = pending is not None

    checks = _checklist([m["text"] for m in ops])
    score = round(100 * sum(checks.values()) / len(CHECKLIST))
    all_waits = ([first_response] if first_response is not None else []) + waits
    flags = []
    if first_response is not None and first_response > SLOW_FIRST_RESPONSE_MIN:
        flags.append(f"Долгий первый ответ ({int(first_response)} мин)")
    if unanswered:
        flags.append("Клиент остался без ответа")
    if not checks["offer"] and not checks["price"]:
        flags.append("Нет предложения и цены")
    if not checks["cta"]:
        flags.append("Нет призыва к покупке/записи")

    return {
        "name": manager_name(ops[0]["sender"]),
        "noReply": False,
        "operatorMessages": len(ops),
        "firstResponseMin": first_response,
        "avgResponseMin": round(sum(all_waits) / len(all_waits), 1) if all_waits else None,
        "unanswered": unanswered,
        "checks": checks,
        "score": score,
        "flags": flags,
    }
