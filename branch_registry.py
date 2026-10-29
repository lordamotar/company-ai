# -*- coding: utf-8 -*-
"""
branch_registry.py — справочник филиалов и нормализация полей звонка.

Зачем:
  * В звонках филиал звучит по-разному («Красный пахарь», «Красный Пильщик», «Рыскулова», «пр. Рыскулова, 103»).
    Справочник (таблица branches, редактируется на странице «Филиалы») приводит их к одному филиалу
    с городом и адресом. Сопоставление делается при выгрузке, а не при анализе: достаточно дополнить справочник,
    и все звонки (старые и новые) пересчитаются без повторного анализа.
  * Здесь же новые поля звонка: город клиента, тип товара, размер диска, юрлицо (компания, БИН).

Схема создаётся автоматически (ensure_schema), отдельной миграции не нужно.
"""

import re
import json
import logging
import difflib

# Новые колонки call_transcriptions
CALL_COLUMNS = [
    ("client_city", "VARCHAR(64)"),       # откуда звонит клиент (не город филиала)
    ("product_type", "VARCHAR(32)"),      # Шины / Диски / Шины и диски / Услуга / Другое
    ("disc_size", "VARCHAR(64)"),         # размер диска (не шины)
    ("is_legal_entity", "BOOLEAN"),       # клиент — юрлицо (счёт на организацию)
    ("company_name", "VARCHAR(256)"),     # название компании / ИП
    ("client_bin", "VARCHAR(12)"),        # БИН / ИИН, если продиктован полностью
]

PRODUCT_TYPES = ["Шины", "Диски", "Шины и диски", "Услуга", "Другое"]

_SCHEMA_READY = False


def ensure_schema(conn):
    """Создаёт таблицу branches и новые колонки звонков (идемпотентно). Коммитит сама."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id SERIAL PRIMARY KEY,
                city VARCHAR(64) NOT NULL,
                name VARCHAR(128) NOT NULL,
                address VARCHAR(256) NOT NULL DEFAULT '',
                aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_branches_city_name ON branches (lower(city), lower(name));")
        for col, typ in CALL_COLUMNS:
            cur.execute(f"ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS {col} {typ};")
        conn.commit()
        cur.close()
        _SCHEMA_READY = True
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Нормализация текста и сопоставление филиала
# ---------------------------------------------------------------------------

KZ_CITIES = [
    "Алматы", "Астана", "Шымкент", "Караганда", "Актобе", "Тараз", "Павлодар", "Усть-Каменогорск",
    "Семей", "Атырау", "Костанай", "Кызылорда", "Уральск", "Петропавловск", "Актау", "Темиртау",
    "Туркестан", "Кокшетау", "Талдыкорган", "Экибастуз", "Рудный", "Жезказган", "Балхаш", "Конаев",
]

_NOISE_WORDS = {
    "г", "город", "ул", "улица", "пр", "проспект", "просп", "шоссе", "ш", "мкр", "микрорайон", "мкрн",
    "тц", "трц", "филиал", "точка", "склад", "база", "пункт", "не", "указан", "aikos", "эйкос",
}


def norm(text):
    """Нижний регистр, ё→е, без знаков, без служебных слов (г., ул., пр., филиал...)."""
    s = (text or "").lower().replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-яәіңғүұқөһ]+", " ", s)
    words = [w for w in s.split() if w not in _NOISE_WORDS]
    return " ".join(words)


def _contains(a, b):
    """a целиком входит в b как набор слов (границы слов)."""
    return f" {a} " in f" {b} "


def _city_in(n):
    """Город, который назван в нормализованной строке (по основе слова: «семей» ~ «семее», «алматы» ~ «алматинский»)."""
    for c in KZ_CITIES:
        nc = norm(c)
        stem = nc[:-2] if len(nc) >= 6 else nc[:-1]
        if len(stem) >= 4 and stem in n:
            return c
    return None


_PREPOSITIONS = {"в", "во", "на", "из", "по", "у", "в", "от", "до"}


def _city_only(n, city):
    """Строка состоит только из названия города (и предлога/окончания): «семей», «в семее», «усть каменогорск»."""
    nc = norm(city)
    stem = nc[:-2] if len(nc) >= 6 else nc[:-1]
    if stem not in n:
        return False
    rest = n.replace(stem, " ", 1).split()
    rest = [w for w in rest if w not in _PREPOSITIONS and (len(w) > 3 or any(ch.isdigit() for ch in w))]
    return not rest


def _score(n, key):
    """(оценка, способ). Нечёткое совпадение (опечатка распознавания) — только для строк от 6 символов."""
    if not n or not key:
        return 0.0, None
    if n == key:
        return 1.0, "exact"
    short, long_ = (n, key) if len(n) <= len(key) else (key, n)
    if len(short) >= 4 and _contains(short, long_):
        return 0.85 + 0.1 * len(short) / len(long_), "contains"
    if len(short) >= 6:
        ratio = difflib.SequenceMatcher(None, n, key).ratio()
        if ratio >= 0.82:
            return ratio, "fuzzy"
    return 0.0, None


def match_branch(raw, branches):
    """
    Сопоставляет написание филиала из звонка со справочником.
    Возвращает {"branch": dict|None, "city": str|None, "how": "exact|contains|fuzzy|city|none"}.
    Неоднозначное совпадение (два филиала почти одинаково подходят) считается несовпадением.
    """
    n = norm(raw)
    if not n:
        return {"branch": None, "city": None, "how": "none"}

    # Если в названии прозвучал город, ищем только среди филиалов этого города
    # (иначе общее слово вроде «Центр» совпало бы с филиалом из другого города).
    city = _city_in(n)
    candidates = list(branches or [])
    if city:
        candidates = [b for b in candidates if norm(b.get("city")) == norm(city)]

    scored = []
    for b in candidates:
        keys = [b.get("name"), b.get("address")] + list(b.get("aliases") or [])
        best = max((_score(n, norm(k)) for k in keys if k), key=lambda x: x[0], default=(0.0, None))
        if best[0] > 0:
            scored.append((best[0], b, best[1]))
    scored.sort(key=lambda x: -x[0])
    if scored:
        top = scored[0]
        if len(scored) == 1 or top[0] - scored[1][0] >= 0.05:
            return {"branch": top[1], "city": top[1].get("city"), "how": top[2]}

    if city:
        # В названии только город («г. Семей», «в Семее»): если в нём ровно один филиал, это он
        if len(candidates) == 1 and _city_only(n, city):
            return {"branch": candidates[0], "city": city, "how": "city"}
        return {"branch": None, "city": city, "how": "none"}
    return {"branch": None, "city": None, "how": "none"}


def is_uninformative(raw):
    """Пустое/общее написание («Филиал Aikos», «г. Не указан») — искать в нём филиал бессмысленно."""
    return not norm(raw)


# ---------------------------------------------------------------------------
# Справочник: чтение и редактирование (работает с курсором psycopg2)
# ---------------------------------------------------------------------------

def _clean_aliases(aliases):
    out, seen = [], set()
    for a in aliases or []:
        a = (str(a) if a is not None else "").strip()[:128]
        if a and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
        if len(out) >= 30:
            break
    return out


def _row(r):
    aliases = r[4]
    if isinstance(aliases, str):
        try:
            aliases = json.loads(aliases)
        except ValueError:
            aliases = []
    return {"id": r[0], "city": r[1], "name": r[2], "address": r[3], "aliases": aliases or []}


def list_branches(cur):
    cur.execute("SELECT id, city, name, address, aliases FROM branches ORDER BY city, name;")
    return [_row(r) for r in cur.fetchall()]


def _validate(city, name, address):
    city, name, address = (city or "").strip(), (name or "").strip(), (address or "").strip()
    if not city:
        raise ValueError("Укажите город.")
    if not name:
        raise ValueError("Укажите название филиала.")
    if len(city) > 64 or len(name) > 128 or len(address) > 256:
        raise ValueError("Слишком длинное значение (город до 64, название до 128, адрес до 256 символов).")
    return city, name, address


def add_branch(cur, city, name, address="", aliases=None):
    city, name, address = _validate(city, name, address)
    cur.execute("""
        INSERT INTO branches (city, name, address, aliases) VALUES (%s, %s, %s, %s::jsonb)
        RETURNING id, city, name, address, aliases;
    """, (city, name, address, json.dumps(_clean_aliases(aliases), ensure_ascii=False)))
    return _row(cur.fetchone())


def update_branch(cur, branch_id, city, name, address="", aliases=None):
    city, name, address = _validate(city, name, address)
    cur.execute("""
        UPDATE branches SET city = %s, name = %s, address = %s, aliases = %s::jsonb WHERE id = %s
        RETURNING id, city, name, address, aliases;
    """, (city, name, address, json.dumps(_clean_aliases(aliases), ensure_ascii=False), branch_id))
    r = cur.fetchone()
    return _row(r) if r else None


def delete_branch(cur, branch_id):
    cur.execute("DELETE FROM branches WHERE id = %s;", (branch_id,))
    return cur.rowcount > 0


def add_alias(cur, branch_id, alias):
    cur.execute("SELECT id, city, name, address, aliases FROM branches WHERE id = %s;", (branch_id,))
    r = cur.fetchone()
    if not r:
        return None
    b = _row(r)
    aliases = _clean_aliases(b["aliases"] + [alias])
    cur.execute("UPDATE branches SET aliases = %s::jsonb WHERE id = %s;",
                (json.dumps(aliases, ensure_ascii=False), branch_id))
    b["aliases"] = aliases
    return b


def classify(raw, branches):
    """Как написание из звонка соотносится со справочником: ("matched"|"unmatched"|"skip", результат match_branch).
    "skip" — общее название, по которому филиал не определить: пусто («Филиал Aikos», «Не указан») или только город."""
    if is_uninformative(raw):
        return "skip", None
    m = match_branch(raw, branches)
    if m["branch"]:
        return "matched", m
    n = norm(raw)
    if m["city"] and _city_only(n, m["city"]):
        return "skip", m
    return "unmatched", m


def branch_usage(cur):
    """
    Как справочник покрывает звонки: сколько звонков у каждого филиала и какие названия из звонков
    не сопоставились (их можно добавить как филиал или как синоним существующего).
    """
    branches = list_branches(cur)
    cur.execute("""
        SELECT branch_address, COUNT(*) FROM call_transcriptions
        WHERE branch_address IS NOT NULL AND btrim(branch_address) <> ''
        GROUP BY branch_address;
    """)
    counts = {b["id"]: 0 for b in branches}
    unmatched, skipped = [], 0
    for raw, cnt in cur.fetchall():
        kind, m = classify(raw, branches)
        if kind == "skip":
            skipped += cnt
        elif kind == "matched":
            counts[m["branch"]["id"]] += cnt
        else:
            unmatched.append({"raw": raw, "calls": cnt, "cityGuess": m["city"]})
    unmatched.sort(key=lambda x: (-x["calls"], x["raw"]))
    for b in branches:
        b["calls"] = counts[b["id"]]
    return {"branches": branches, "unmatched": unmatched, "uninformativeCalls": skipped}


# ---------------------------------------------------------------------------
# Нормализация новых полей звонка
# ---------------------------------------------------------------------------

def normalize_product_type(value):
    v = (str(value) if value is not None else "").strip().lower()
    if not v or v in ("null", "none", "не указано", "не указан", "—", "-"):
        return None
    has_tire = bool(re.search(r"\bшин(?:ы|а|у|е|ам|ами|ах|ой)?\b|резин|покрышк", v))
    has_disc = "диск" in v
    if has_tire and has_disc:
        return "Шины и диски"
    if has_disc:
        return "Диски"
    if has_tire:
        return "Шины"
    if any(w in v for w in ("услуг", "монтаж", "сервис", "хранен", "балансир", "ремонт")):
        return "Услуга"
    return "Другое"


def normalize_bin(value):
    """БИН/ИИН: ровно 12 цифр (пробелы и дефисы допускаются), иначе None."""
    digits = re.sub(r"\D", "", str(value) if value is not None else "")
    return digits if len(digits) == 12 else None


def bin_checksum_ok(digits):
    """Проверка контрольной цифры БИН/ИИН (алгоритм РК). Нужна, чтобы заметить ошибку распознавания цифр."""
    if not digits or not re.fullmatch(r"\d{12}", digits):
        return False
    d = [int(c) for c in digits]
    s = sum((i + 1) * d[i] for i in range(11)) % 11
    if s == 10:
        s = sum(((i + 3) % 11 if (i + 3) % 11 != 0 else 11) * d[i] for i in range(11)) % 11
    return s != 10 and s == d[11]


def clean_text(value, limit):
    v = (str(value) if value is not None else "").strip()
    if not v or v.lower() in ("null", "none", "не указано", "—", "-"):
        return None
    return v[:limit]


def normalize_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in ("true", "да", "yes", "1"):
        return True
    if v in ("false", "нет", "no", "0"):
        return False
    return None
