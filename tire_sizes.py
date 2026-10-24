# -*- coding: utf-8 -*-
"""
tire_sizes.py — проверка и нормализация типоразмеров шин.

Зачем: в звонках диктуют номера телефонов и заказов («707 512 03 06», «268 01 33»),
и раньше такие цифры попадали в поле «типоразмер» как «512/03/06».
Здесь размер принимается, только если его числа физически возможны для шины.

Поддерживаются:
  метрические   205/55 R16, 185/75 R16C, 445/45 R19.5, 235/75 R17.5, 225/45ZR17
  без профиля   195 R14C
  дюймовые      10.00 R20, 9.00-16, 8.3-20, 18.4 R34, 10-16.5, 7.00-12, 31x10.5 R15
  только диаметр R15 (клиент назвал лишь радиус)
"""

import re

_NUM = r"\d{1,2}(?:[.,]\d{1,2})?"

_METRIC = re.compile(r"(?<!\d)(\d{3})\s*[/\\ ]\s*(\d{2})\s*(Z?R|-|\s)?\s*(\d{2}(?:[.,]5)?)\s*(C|LT)?(?!\d|[.,]\d)", re.I)
_NO_PROFILE = re.compile(r"(?<!\d)(\d{3})\s*R\s*(\d{2})\s*(C|LT)?(?!\d|[.,]\d|/)", re.I)
_INCH = re.compile(r"(?<!\d)(?<![.,]\d)(" + _NUM + r")\s*(R|-)\s*(\d{2}(?:[.,]5)?)(?!\d|[.,]\d)", re.I)
_FLOTATION = re.compile(r"(?<!\d)(\d{2})\s*[xх×]\s*(" + _NUM + r")\s*R\s*(\d{2})(?!\d|[.,]\d)", re.I)
_RIM_ONLY = re.compile(r"^R\s*(\d{2}(?:[.,]5)?)\s*(C)?$", re.I)


def _rim_ok(rim):
    r = float(rim.replace(",", "."))
    return 10 <= r <= 26


def _metric(width, profile, rim, suffix=""):
    w, p = int(width), int(profile)
    if not (125 <= w <= 455 and w % 5 == 0):
        return None
    if not (25 <= p <= 95 and p % 5 == 0):
        return None
    if not _rim_ok(rim):
        return None
    return f"{w}/{p} R{rim.replace(',', '.')}{(suffix or '').upper()}"


def _parse_one(s):
    s = s.strip().upper().replace("Р", "R").replace("С", "C")   # кириллические Р и С → латиница
    if not s:
        return None
    m = _RIM_ONLY.match(s)
    if m:
        return f"R{m.group(1).replace(',', '.')}{(m.group(2) or '')}" if _rim_ok(m.group(1)) else None
    m = _METRIC.search(s)
    if m:
        return _metric(m.group(1), m.group(2), m.group(4), m.group(5))
    m = _NO_PROFILE.search(s)
    if m:
        w = int(m.group(1))
        if 125 <= w <= 455 and w % 5 == 0 and _rim_ok(m.group(2)):
            return f"{w} R{m.group(2)}{(m.group(3) or '').upper()}"
        return None
    m = _FLOTATION.search(s)
    if m:
        if 25 <= int(m.group(1)) <= 45 and _rim_ok(m.group(3)):
            return f"{m.group(1)}x{m.group(2).replace(',', '.')} R{m.group(3)}"
        return None
    m = _INCH.search(s)
    if m:
        first = float(m.group(1).replace(",", "."))
        rim = float(m.group(3).replace(",", "."))
        if 5 <= first <= 30 and 8 <= rim <= 54:
            sep = " R" if m.group(2).upper() == "R" else "-"
            return f"{m.group(1).replace(',', '.')}{sep}{m.group(3).replace(',', '.')}"
    return None


def normalize_tire_size(value):
    """
    Нормализует значение поля «типоразмер» (от модели или из БД).
    Несколько размеров («205/60 R16, 235/55 R18») проверяются по отдельности.
    Возвращает строку с допустимыми размерами через « / » или None, если допустимых нет.
    """
    if value is None:
        return None
    parts = re.split(r"\s*(?:,|;|\s/\s|\sили\s|\sи\s)\s*", str(value), flags=re.I)
    valid = []
    for part in parts:
        norm = _parse_one(part)
        if norm and norm not in valid:
            valid.append(norm)
    return " / ".join(valid) if valid else None


def find_tire_size_in_text(text):
    """Первый допустимый типоразмер в тексте расшифровки (запасной путь, если модель размер не вернула)."""
    t = (text or "").upper().replace("Р", "R")
    for rx in (_METRIC, _NO_PROFILE, _FLOTATION):
        for m in rx.finditer(t):
            norm = _parse_one(m.group(0))
            if norm:
                return norm
    return None
