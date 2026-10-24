# -*- coding: utf-8 -*-
"""
lead_scoring.py — оценка клиента (A/B/C/D/нецелевой), причина нецелевого звонка и следующий шаг.

ВЫКЛЮЧЕНО ПО УМОЛЧАНИЮ. Пока ENABLE_LEAD_SCORING не равно true:
  * блок не попадает в промпт (версия промпта не меняется);
  * поля не сохраняются в БД.

Включение (README, раздел «Оценка клиента и следующий шаг»):
  1. python apply_migrations.py migrations/lead_scoring.sql
  2. в .env: ENABLE_LEAD_SCORING=true
  3. перезапустить воркер и веб-сервер.

Уведомления руководителю («шаг обещан и не выполнен») и передача причин маркетингу — следующий этап,
после автоматической загрузки записей и статистики АТС (CDR).
"""

import os
import json

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def is_enabled():
    return os.getenv("ENABLE_LEAD_SCORING", "false").strip().lower() in ("1", "true", "yes", "да")


GRADES = {
    "A": "размер/товар известен, нужное есть в наличии, клиент готов купить сейчас или оформляет бронь / счёт",
    "B": "потребность ясна, но клиент откладывает решение: сравнивает цены, ждёт сезона или поступления, советуется",
    "C": "клиент просто узнаёт цены или наличие без конкретики (нет размера, срока, намерения купить)",
    "D": "покупка маловероятна: нужного товара нет и замена не подошла, нет филиала в городе клиента, цена категорически не устроила",
    "Нецелевой": "звонящий не является потенциальным покупателем наших товаров и услуг",
}

NON_TARGET_REASONS = [
    "Ошибся номером",
    "Нужен товар или услуга, которых у нас нет",
    "Другой город без филиала и доставки",
    "Реклама, спам или предложение услуг",
    "Пустой или оборвавшийся звонок",
    "Другое",
]

NEXT_STEP_TYPES = [
    "Бронь оформлена",
    "Перезвонить клиенту",
    "Написать в WhatsApp",
    "Выставить счёт",
    "Клиент приедет",
    "Клиент перезвонит сам",
    "Другое",
    "Нет шага",
]


def _norm(s):
    return " ".join(str(s or "").strip().lower().replace("ё", "е").split())


def _match(value, options):
    v = _norm(value)
    for opt in options:
        if _norm(opt) == v:
            return opt
    return None


def prompt_block():
    """Текст блока для промпта или пустая строка, если функция выключена."""
    if not is_enabled():
        return ""
    import re
    with open(os.path.join(PROMPTS_DIR, "lead_scoring.md"), encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"\A\s*<!--.*?-->\s*", "", text, count=1, flags=re.S)
    text = text.replace("{{GRADES}}", "\n".join(f"- «{k}» — {v}" for k, v in GRADES.items()))
    text = text.replace("{{NON_TARGET_REASONS}}", "\n".join(f"- «{r}»" for r in NON_TARGET_REASONS))
    text = text.replace("{{NEXT_STEP_TYPES}}", "\n".join(f"- «{t}»" for t in NEXT_STEP_TYPES))
    return text.strip()


def extract_lead_fields(analysis_data):
    """Достаёт и проверяет поля из ответа модели. Значения не из списков отбрасываются (None)."""
    lead = (analysis_data or {}).get("lead") or {}
    grade = _match(lead.get("grade"), GRADES.keys())
    reason = _match(lead.get("non_target_reason"), NON_TARGET_REASONS) if grade == "Нецелевой" else None
    step = lead.get("next_step") or {}
    step_type = _match(step.get("type"), NEXT_STEP_TYPES)
    next_step = None
    if step_type:
        owner = step.get("owner") if step.get("owner") in ("Менеджер", "Клиент") else None
        next_step = {
            "type": step_type,
            "description": (step.get("description") or None),
            "deadline": (step.get("deadline") or None),
            "owner": owner,
        }
    return {
        "lead_grade": grade,
        "lead_grade_reason": (lead.get("grade_reason") or None) if grade else None,
        "non_target_reason": reason,
        "next_step": json.dumps(next_step, ensure_ascii=False) if next_step else None,
    }
