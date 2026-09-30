"""
Защита аналитики от ошибок распознавания речи (ASR) в приветствии менеджера.

Проблема: телефонная связь, шум, тихая речь и смешение казахского/русского
искажают в расшифровке имя менеджера и название компании
(«Эйкос» -> «Дельта компаниясы», «Диас» -> «Денис» / «Адос» и т.п.)
или вовсе «съедают» их. Проверить по тексту, КАК менеджер представился, нельзя.

Принцип: оцениваем только ФАКТ приветствия/представления, но не его содержание.
  1. На входе в метрики — пересчитываем этап «Приветствие» по диалогу:
       есть приветствие или попытка представиться -> passed (содержание не проверяем);
       внутренний звонок коллеги                   -> not_applicable;
       нет вообще ничего                           -> failed (реальная проблема).
  2. На входе в ИИ — маскируем имя/компанию в первых репликах менеджера
     ([имя], [компания]) и убираем из чек-листа утверждения о «чужом имени».
  3. На выходе ИИ — страховочная сеть: выбрасываем выводы о «чужом имени /
     чужой компании», но НЕ трогаем другие замечания, где просто встречается
     слово «приветствие».

Модуль не хранит список «типичных искажений» — он работает для любого имени.
"""
import copy
import re
from typing import List

GUARD_VERSION = "asr-guard-2"

# сколько первых реплик менеджера считаем «приветствием»
GREETING_TURNS = 2

_GREETING_WORDS = re.compile(
    r"здравствуй|здрасьте|добр(?:ый|ое|ого)\s+(?:день|утро|вечер)|доброго|привет|"
    r"са[лл]ем|сәлем|салам|сәлеметсіз|саламатсыз|сәламатсыз|қайырлы|кайырлы|ассалау|ассалам",
    re.IGNORECASE,
)
_SELF_ID = re.compile(
    r"компани|компаниясы|эйкос|айкос|aikos|оператор|менеджер|меня\s+зовут|"
    r"(?:^|[\s,.!?])(?:это|я)\s+[А-ЯЁӘІҢҒҮҰҚӨҺA-Z]",
    re.IGNORECASE,
)
_INTERNAL = re.compile(r"внутренн|коллег|между сотрудник|сотрудник(?:ом|ами)? компании", re.IGNORECASE)

# Маскирование самопредставления в тексте реплики
_NAME = r"[А-ЯЁӘІҢҒҮҰҚӨҺA-Z][а-яёәіңғүұқөһa-z\-]+"
_MASKS = [
    # «Дельта компаниясы», «Эйкос компаниясы»
    (re.compile(rf"[«\"]?{_NAME}[»\"]?\s+компаниясы", re.IGNORECASE), "[компания] компаниясы"),
    # «компания Эйкос», «компания «Дельта»»
    (re.compile(rf"(компани[яиюей]\w*)\s+[«\"]?{_NAME}[»\"]?", re.IGNORECASE), r"\1 [компания]"),
    # «оператор Денис», «менеджер Адос», «меня зовут Олжас», «это Руслан»
    (re.compile(rf"(оператор|менеджер|меня\s+зовут|(?:^|(?<=[\s,.!?]))это)\s+{_NAME}", re.IGNORECASE), r"\1 [имя]"),
    # «Эйкос, Артём, здравствуйте» — имя сразу после названия компании
    (re.compile(rf"(\[компания\][»\"]?\s*[,–-]\s*){_NAME}"), r"\1[имя]"),
]

# Выводы ИИ, которые утверждают «представился не тем / назвал чужую компанию».
# Сознательно НЕ включает слово «приветствие» само по себе.
_MISMATCH = re.compile(
    r"чуж\w*\s+(?:им[яеёи]\w*|компан\w*|организац\w*|бренд\w*|юр\w*|наименован\w*)|"
    r"(?:им[яеёи]\w*|компан\w*|организац\w*)\s+чуж\w*|"
    r"не\s+сво\w+\s+им\w*|друг\w+\s+(?:им[яеёи]\w*|компан\w*|организац\w*)|"
    r"сторонн\w*\s+(?:компан\w*|организац\w*|наименован\w*|бренд\w*)|"
    r"вымышлен\w*|псевдоним\w*|самоидентификац\w*|"
    r"путает\s+имен\w*|путаниц\w*\s+в\s+(?:представлен|имен)\w*|"
    r"представ\w*ся\s+(?:как|другим|иначе|по-разному|неправильн)\w*|"
    r"неверн\w*\s+(?:им[яеёи]\w*|назван\w*\s+компан\w*)|"
    r"дельта",
    re.IGNORECASE,
)

NEUTRAL_PASSED = ("Приветствие/представление есть. Имя и название компании в расшифровке "
                  "не проверяются: возможна ошибка распознавания речи.")
NEUTRAL_INTERNAL = "Внутренний разговор сотрудников — этап приветствия клиента неприменим."


def is_greeting_stage(title: str) -> bool:
    return "приветств" in (title or "").lower()


def mentions_identity_mismatch(text: str) -> bool:
    return bool(_MISMATCH.search(text or ""))


def mask_identity(text: str) -> str:
    """Скрывает имя менеджера и название компании в реплике-приветствии."""
    out = text or ""
    for rx, repl in _MASKS:
        out = rx.sub(repl, out)
    return out


def manager_opening(call: dict, turns: int = GREETING_TURNS) -> str:
    lines = [(t.get("text") or "").strip() for t in (call.get("dialogue") or [])
             if t.get("role") == "manager" and (t.get("text") or "").strip()]
    return " ".join(lines[:turns])


def has_greeting(call: dict) -> bool:
    opening = manager_opening(call)
    return bool(_GREETING_WORDS.search(opening) or _SELF_ID.search(opening))


def _item_state(item: dict) -> str:
    if item.get("not_applicable") or item.get("status") == "not_applicable":
        return "na"
    if item.get("passed") is True or item.get("status") == "passed":
        return "passed"
    return "failed"


def _set_state(item: dict, state: str, desc: str) -> None:
    item["status"] = {"na": "not_applicable"}.get(state, state)
    item["passed"] = None if state == "na" else state == "passed"
    item["not_applicable"] = state == "na"
    item["desc"] = desc
    item["asr_normalized"] = True


def normalize_call(call: dict) -> dict:
    """Возвращает КОПИЮ звонка с пересчитанным этапом «Приветствие».
    Исходные данные (и БД) не изменяются."""
    items = call.get("checklist")
    if not isinstance(items, list) or not any(is_greeting_stage(i.get("title")) for i in items):
        return call
    c = copy.copy(call)
    c["checklist"] = [dict(i) for i in items]
    opening = manager_opening(c)
    for item in c["checklist"]:
        if not is_greeting_stage(item.get("title")):
            continue
        state = _item_state(item)
        desc = str(item.get("desc") or "")
        if state == "na":
            continue
        if _INTERNAL.search(desc) or _INTERNAL.search(str(c.get("callSummary") or "")):
            _set_state(item, "na", NEUTRAL_INTERNAL)
        elif state == "failed" and has_greeting(c):
            _set_state(item, "passed", NEUTRAL_PASSED)
        elif state == "failed":
            quote = mask_identity(opening)[:120]
            _set_state(item, "failed",
                       f"«{quote}» • В начале разговора нет приветствия и представления." if quote
                       else "В начале разговора нет приветствия и представления.")
        elif mentions_identity_mismatch(desc):
            _set_state(item, "passed", NEUTRAL_PASSED)
    return c


def script_score(checklist: List[dict]):
    """Процент выполненных этапов среди применимых (та же формула, что в выгрузке). 100, если применимых нет."""
    applicable = [i for i in checklist if _item_state(i) != "na"]
    if not applicable:
        return 100
    return int(sum(1 for i in applicable if _item_state(i) == "passed") * 100 / len(applicable))


def normalize_calls(calls: List[dict]) -> List[dict]:
    return [normalize_call(c) for c in calls]


def masked_dialogue_turns(call: dict, turns: int = GREETING_TURNS):
    """Реплики диалога, где первые `turns` реплик менеджера замаскированы."""
    seen = 0
    for t in call.get("dialogue") or []:
        text = (t.get("text") or "").strip()
        if t.get("role") == "manager" and text and seen < turns:
            seen += 1
            text = mask_identity(text)
        yield t.get("role"), text


def drop_mismatch_sentences(text: str) -> str:
    """Удаляет из свободного текста предложения с утверждениями о «чужом имени/компании»."""
    if not text:
        return text
    parts = re.split(r"(?<=[.!?])\s+", str(text))
    kept = [p for p in parts if not mentions_identity_mismatch(p)]
    return " ".join(kept).strip()
