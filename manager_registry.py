"""
Справочник менеджеров: приводит имя, услышанное в звонке, к реальному сотруднику.

Зачем: распознавание речи искажает имена (Нұржан, Рустам, Денис…), и без справочника
каждое искажение становится «новым менеджером» в отчётах. Здесь имя сопоставляется
со списком из managers.json; если уверенного совпадения нет — возвращается None
(звонок пойдёт на «Оператор», а не на выдуманного сотрудника).

Только стандартная библиотека. Файл справочника перечитывается при изменении.
"""
import difflib
import json
import logging
import os
import re
from typing import List, Optional

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "managers.json")
FUZZY_THRESHOLD = 0.82      # порог похожести (0..1) для нечёткого совпадения
AMBIGUITY_GAP = 0.06        # если два имени почти одинаково похожи — не угадываем

_KZ = str.maketrans({"ұ": "у", "ү": "у", "қ": "к", "ғ": "г", "ә": "а", "ө": "о", "і": "и",
                     "ң": "н", "һ": "х", "ё": "е", "э": "е", "й": "и"})
_cache = {"mtime": None, "data": None}


def _norm(name: str) -> str:
    s = (name or "").strip().lower().translate(_KZ)
    return re.sub(r"[^a-zа-я]", "", s)


def _load() -> List[dict]:
    try:
        mtime = os.path.getmtime(REGISTRY_PATH)
        if _cache["mtime"] != mtime:
            with open(REGISTRY_PATH, encoding="utf-8") as f:
                _cache["data"] = json.load(f).get("managers", [])
            _cache["mtime"] = mtime
    except (OSError, ValueError) as e:
        logging.warning("[Справочник менеджеров] Не удалось прочитать %s: %s", REGISTRY_PATH, e)
        _cache["data"] = _cache["data"] or []
    return _cache["data"]


def canonical_names() -> List[str]:
    return [m["name"] for m in _load()]


def resolve(name: Optional[str]) -> Optional[str]:
    """Возвращает каноническое имя сотрудника или None, если уверенного совпадения нет."""
    key = _norm(name)
    if len(key) < 3:
        return None
    entries = _load()
    for m in entries:                                   # 1. точное совпадение с именем/псевдонимом
        if key in {_norm(m["name"])} | {_norm(a) for a in m.get("aliases", [])}:
            return m["name"]
    scored = []                                         # 2. нечёткое совпадение
    for m in entries:
        variants = [m["name"]] + list(m.get("aliases", []))
        best = max(difflib.SequenceMatcher(None, key, _norm(v)).ratio() for v in variants)
        scored.append((best, m["name"]))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= FUZZY_THRESHOLD:
        if len(scored) > 1 and scored[0][0] - scored[1][0] < AMBIGUITY_GAP:
            return None
        return scored[0][1]
    return None


def resolve_or_warn(name: Optional[str], context: str = "") -> Optional[str]:
    """resolve() + предупреждение в лог для неизвестных имён."""
    canon = resolve(name)
    if name and not canon and len(_norm(name)) >= 3:
        logging.warning("[Справочник менеджеров] Имя «%s» не найдено в managers.json%s. "
                        "Звонок записан на «Оператор». Если это новый сотрудник — добавьте его в managers.json.",
                        name, f" ({context})" if context else "")
    return canon


def find_in_text(text: str, limit: int = 300) -> Optional[str]:
    """Ищет в начале текста имя любого сотрудника из справочника."""
    head = (text or "")[:limit]
    letters = "А-Яа-яЁёӘәІіҢңҒғҮүҰұҚқӨөҺһA-Za-z"
    for m in _load():
        for variant in [m["name"]] + list(m.get("aliases", [])):   # имя и его написания (в т.ч. казахские)
            if re.search(rf"(?<![{letters}]){re.escape(variant)}(?![{letters}])", head, re.IGNORECASE):
                return m["name"]
    return None
