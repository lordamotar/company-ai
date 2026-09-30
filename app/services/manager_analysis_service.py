"""
Детальный анализ работы менеджера за период.

Что считается по данным (без ИИ, детерминированно):
  - метрики звонков, CSAT, вежливость, конфликты;
  - выполнение каждого этапа скрипта (только по звонкам, где этап применим);
  - упущенные допродажи, исходы звонков, причины отказов;
  - паузы/тишина, темп речи;
  - динамика по неделям (или по дням для коротких периодов);
  - сравнение с командой за тот же период;
  - рейтинг «проблемных» звонков.

ИИ (Gemini) получает уже посчитанные факты + дословные диалоги худших звонков
и возвращает структурированный разбор слабых мест. Все ссылки ИИ на звонки
и цитаты проверяются по реальным данным (поле verified).
"""
import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import BASE_DIR
from app.services import asr_guard

logger = logging.getLogger(__name__)

CACHE_FILE = Path(os.getenv("MANAGER_AI_CACHE", BASE_DIR / "app" / "cache" / "manager_ai_reports.json"))

DEFAULT_GEMINI_MODELS = ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-flash-latest"]

# Сколько «худших» звонков отдаём ИИ вместе с полным диалогом
AI_WORST_CALLS = 10
AI_DIALOGUE_CHARS = 2600
MIN_CALLS_FOR_TRENDS = 3


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------
def parse_call_date(value) -> Optional[date]:
    """'10.09.2026' -> date. Пустое/некорректное -> None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def parse_iso_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        m = re.search(r"\d+(?:[.,]\d+)?", str(value))
        return float(m.group(0).replace(",", ".")) if m else None


def _avg(values) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _pct(part, total) -> Optional[int]:
    return round(part * 100 / total) if total else None


def _silence_pct(call: dict) -> Optional[float]:
    text = (call.get("acoustics") or {}).get("silence") or ""
    m = re.search(r"\((\d+(?:\.\d+)?)\s*%\)", text)
    return float(m.group(1)) if m else None


def _tempo(call: dict) -> Optional[float]:
    return _num((call.get("acoustics") or {}).get("tempo"))


def _politeness(call: dict) -> Optional[float]:
    tone = call.get("managerTone") or {}
    val = _num(tone.get("politenessScore"))
    if val is None:
        val = _num(call.get("managerPoliteness"))
    return val


def _call_manager(call: dict) -> str:
    return (call.get("manager") or "Оператор").strip()


def _checklist(call: dict) -> List[dict]:
    items = call.get("checklist")
    return items if isinstance(items, list) else []


def _item_state(item: dict) -> str:
    """passed | failed | na"""
    if item.get("not_applicable") or item.get("status") == "not_applicable":
        return "na"
    if item.get("passed") is True or item.get("status") == "passed":
        return "passed"
    return "failed"


def _is_upsell_stage(title: str) -> bool:
    # в данных этап называется «Доп прдажа» (с опечаткой) — ищем по корню «доп»
    return "доп" in (title or "").lower()


def _fmt_dur(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


# ---------------------------------------------------------------------------
# Выборка
# ---------------------------------------------------------------------------
def filter_calls(calls: List[dict], manager: Optional[str], date_from: Optional[date],
                 date_to: Optional[date]) -> List[dict]:
    result = []
    for c in calls:
        if manager and _call_manager(c).lower() != manager.strip().lower():
            continue
        d = parse_call_date(c.get("date"))
        if (date_from or date_to) and d is None:
            continue
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        result.append(c)
    return result


def list_managers(calls: List[dict]) -> List[dict]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for c in calls:
        groups[_call_manager(c)].append(c)
    out = []
    for name, items in groups.items():
        dates = [d for d in (parse_call_date(c.get("date")) for c in items) if d]
        out.append({
            "name": name,
            "calls": len(items),
            "date_min": min(dates).isoformat() if dates else None,
            "date_max": max(dates).isoformat() if dates else None,
        })
    out.sort(key=lambda m: (-m["calls"], m["name"]))
    return out


def list_periods(calls: List[dict]) -> Dict[str, Optional[str]]:
    dates = [d for d in (parse_call_date(c.get("date")) for c in calls) if d]
    return {
        "date_min": min(dates).isoformat() if dates else None,
        "date_max": max(dates).isoformat() if dates else None,
    }


# ---------------------------------------------------------------------------
# Метрики
# ---------------------------------------------------------------------------
def _stage_stats(calls: List[dict]) -> List[dict]:
    stages: Dict[str, dict] = {}
    order: List[str] = []
    for c in calls:
        for item in _checklist(c):
            title = item.get("title") or "Без названия"
            st = stages.get(title)
            if st is None:
                st = stages[title] = {"title": title, "applicable": 0, "passed": 0, "failed": 0,
                                      "na": 0, "failed_calls": []}
                order.append(title)
            state = _item_state(item)
            if state == "na":
                st["na"] += 1
            elif state == "passed":
                st["applicable"] += 1
                st["passed"] += 1
            else:
                st["applicable"] += 1
                st["failed"] += 1
                st["failed_calls"].append(c.get("id"))
    result = []
    for title in order:
        st = stages[title]
        st["pass_rate"] = _pct(st["passed"], st["applicable"])
        result.append(st)
    return result


def _call_badness(call: dict) -> float:
    """Чем больше, тем хуже звонок. Нужен для ранжирования проблемных звонков."""
    score = 0.0
    failed = sum(1 for i in _checklist(call) if _item_state(i) == "failed")
    upsell_failed = any(_is_upsell_stage(i.get("title")) and _item_state(i) == "failed"
                        for i in _checklist(call))
    # допродажа проваливается почти везде — она не должна забивать остальные сигналы
    score += (failed - (1 if upsell_failed else 0)) * 15 + (3 if upsell_failed else 0)
    script = _num(call.get("scriptScore"))
    if script is not None:
        score += (100 - script) * 0.4
    csat = _num(call.get("csat"))
    if csat is not None and csat < 5:
        score += (5 - csat) * 12
    if call.get("conflict"):
        score += 40
    if (call.get("saleStatus") or "") == "Отказ от покупки":
        score += 10
    sil = _silence_pct(call)
    if sil is not None and sil > 40:   # тишина включает и паузы клиента — вес небольшой
        score += (sil - 40) * 0.3
    return round(score, 1)


def _call_brief(call: dict) -> dict:
    failed = [i.get("title") for i in _checklist(call)
              if _item_state(i) == "failed" and not _is_upsell_stage(i.get("title"))]
    upsell_missed = any(_is_upsell_stage(i.get("title")) and _item_state(i) == "failed"
                        for i in _checklist(call))
    return {
        "id": call.get("id"),
        "date": call.get("date"),
        "time": call.get("time"),
        "topic": call.get("topic"),
        "duration": call.get("duration"),
        "direction": call.get("direction"),
        "script_score": _num(call.get("scriptScore")),
        "csat": _num(call.get("csat")),
        "conflict": bool(call.get("conflict")),
        "sale_status": call.get("saleStatus"),
        "failed_stages": failed,
        "upsell_missed": upsell_missed,
        "silence_pct": _silence_pct(call),
        "summary": call.get("callSummary") or call.get("summary"),
        "badness": _call_badness(call),
    }


def _trend(calls: List[dict], date_from: Optional[date], date_to: Optional[date]) -> Dict:
    dated = [(parse_call_date(c.get("date")), c) for c in calls]
    dated = [(d, c) for d, c in dated if d]
    if len(dated) < MIN_CALLS_FOR_TRENDS:
        return {"granularity": None, "points": []}
    lo = date_from or min(d for d, _ in dated)
    hi = date_to or max(d for d, _ in dated)
    by_day = (hi - lo).days <= 14
    buckets: Dict[date, List[dict]] = defaultdict(list)
    for d, c in dated:
        key = d if by_day else d - timedelta(days=d.weekday())
        buckets[key].append(c)
    points = []
    for key in sorted(buckets):
        items = buckets[key]
        label = key.strftime("%d.%m") if by_day else \
            f"{key.strftime('%d.%m')}–{(key + timedelta(days=6)).strftime('%d.%m')}"
        stage_rates = [s["pass_rate"] for s in _stage_stats(items)
                       if s["pass_rate"] is not None and not _is_upsell_stage(s["title"])]
        points.append({
            "label": label,
            "calls": len(items),
            "script": _avg([_num(c.get("scriptScore")) for c in items]),
            "csat": _avg([_num(c.get("csat")) for c in items]),
            "stage_pass": _avg(stage_rates),
        })
    return {"granularity": "day" if by_day else "week", "points": points}


def build_metrics(calls: List[dict], team_calls: Optional[List[dict]] = None,
                  date_from: Optional[date] = None, date_to: Optional[date] = None) -> dict:
    n = len(calls)
    if n == 0:
        return {"calls": 0}

    durations = [_num(c.get("durationSec")) for c in calls]
    durations = [d for d in durations if d is not None]
    total_sec = sum(durations)

    stages = _stage_stats(calls)
    upsell_stage = next((s for s in stages if _is_upsell_stage(s["title"])), None)

    outcomes = Counter((c.get("saleStatus") or "Не определён") for c in calls)
    lost_reasons = Counter((c.get("lostReason") or "").strip() for c in calls if (c.get("lostReason") or "").strip())
    won = outcomes.get("Продажа состоялась", 0) + outcomes.get("Бронь оформлена", 0)

    silences = [_silence_pct(c) for c in calls]
    long_silence_calls = [c.get("id") for c in calls if (_silence_pct(c) or 0) > 30]

    csat_dist = Counter(int(_num(c.get("csat"))) for c in calls if _num(c.get("csat")) is not None)
    briefs = [_call_brief(c) for c in calls]
    problem_calls = sorted(briefs, key=lambda b: -b["badness"])[:10]
    problem_calls = [b for b in problem_calls if b["badness"] > 0]

    metrics = {
        "calls": n,
        "in_calls": sum(1 for c in calls if c.get("direction") == "in"),
        "out_calls": sum(1 for c in calls if c.get("direction") == "out"),
        "total_duration": _fmt_dur(total_sec),
        "avg_duration": _fmt_dur(total_sec / len(durations)) if durations else None,
        "avg_script": _avg([_num(c.get("scriptScore")) for c in calls]),
        "avg_csat": _avg([_num(c.get("csat")) for c in calls]),
        "avg_politeness": _avg([_politeness(c) for c in calls]),
        "conflicts": sum(1 for c in calls if c.get("conflict")),
        "conflict_calls": [c.get("id") for c in calls if c.get("conflict")],
        "low_csat_calls": [c.get("id") for c in calls
                           if _num(c.get("csat")) is not None and _num(c.get("csat")) <= 3],
        "csat_distribution": {str(k): csat_dist[k] for k in sorted(csat_dist)},
        "stages": stages,
        "upsell": {
            "applicable": upsell_stage["applicable"] if upsell_stage else 0,
            "offered": upsell_stage["passed"] if upsell_stage else 0,
            "missed": upsell_stage["failed"] if upsell_stage else 0,
            "missed_calls": upsell_stage["failed_calls"] if upsell_stage else [],
            "real_upsells": sum(1 for c in calls if c.get("hasUpsell")),
        },
        "outcomes": dict(outcomes),
        "won_calls": won,
        "won_rate": _pct(won, n),
        "lost_reasons": dict(lost_reasons.most_common(8)),
        "avg_silence_pct": _avg(silences),
        "long_silence_calls": long_silence_calls,
        "avg_tempo": _avg([_tempo(c) for c in calls]),
        "trend": _trend(calls, date_from, date_to),
        "problem_calls": problem_calls,
    }

    if team_calls:
        team_stages = {s["title"]: s["pass_rate"] for s in _stage_stats(team_calls)}
        metrics["team"] = {
            "calls": len(team_calls),
            "avg_script": _avg([_num(c.get("scriptScore")) for c in team_calls]),
            "avg_csat": _avg([_num(c.get("csat")) for c in team_calls]),
            "avg_politeness": _avg([_politeness(c) for c in team_calls]),
            "avg_silence_pct": _avg([_silence_pct(c) for c in team_calls]),
            "won_rate": _pct(
                sum(1 for c in team_calls if c.get("saleStatus") in ("Продажа состоялась", "Бронь оформлена")),
                len(team_calls)),
            "stage_pass": team_stages,
        }
    return metrics


def build_analysis(all_calls: List[dict], manager: str, date_from: Optional[date],
                   date_to: Optional[date]) -> dict:
    all_calls = asr_guard.normalize_calls(all_calls)
    selected = filter_calls(all_calls, manager, date_from, date_to)
    team = filter_calls(all_calls, None, date_from, date_to)
    return {
        "manager": manager,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "metrics": build_metrics(selected, team, date_from, date_to),
    }


# ---------------------------------------------------------------------------
# ИИ-разбор
# ---------------------------------------------------------------------------
def _dialogue_text(call: dict, limit: int = AI_DIALOGUE_CHARS) -> str:
    # имя менеджера и компания в приветствии маскируются: ASR их искажает
    lines = []
    for role, text in asr_guard.masked_dialogue_turns(call):
        if text:
            lines.append(f"{'М' if role == 'manager' else 'К'}: {text}")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit] + "\n[...диалог обрезан]"


def _full_dialogue_text(call: dict) -> str:
    return "\n".join((t.get("text") or "") for t in (call.get("dialogue") or []))


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", (text or "").lower(), flags=re.UNICODE).strip()


def _fingerprint(manager: str, date_from, date_to, calls: List[dict]) -> str:
    prompt_hash = hashlib.sha256(_load_system_prompt_template().encode("utf-8")).hexdigest()[:12]
    parts = [asr_guard.GUARD_VERSION, prompt_hash, manager.lower(), str(date_from), str(date_to)]
    for c in sorted(calls, key=lambda x: x.get("id") or 0):
        parts.append(json.dumps([
            c.get("id"), c.get("scriptScore"), c.get("csat"), c.get("conflict"),
            [(i.get("title"), _item_state(i)) for i in _checklist(c)],
        ], ensure_ascii=False))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _load_system_prompt_template() -> str:
    """Загружает системный промпт из prompts/manager_analysis_prompt.md (можно править без изменения кода).
    HTML-комментарии <!-- ... --> вырезаются и в модель не попадают."""
    names = ("manager_analysis_prompt.md", "manager_analysis_prompt.txt")   # .txt — старое имя, запасной вариант
    dirs = [BASE_DIR / "prompts", Path("/app/prompts")]
    for d in dirs:
        for name in names:
            p = d / name
            if not p.exists():
                continue
            try:
                content = re.sub(r"<!--.*?-->", "", p.read_text(encoding="utf-8"), flags=re.DOTALL).strip()
                if content:
                    return content
            except Exception as e:
                logger.warning("[Manager AI] Ошибка чтения промпта из %s: %s", p, e)

    # Резервный промпт, если внешний файл не найден (краткая копия prompts/manager_analysis_prompt.md)
    logger.warning("[Manager AI] Файл промпта не найден, используется встроенный")
    return """Ты — руководитель отдела продаж сети шинных центров «Эйкос» (Aikos, Казахстан) и строгий аудитор качества звонков.
Сделай честный детальный разбор работы менеджера «{manager}» за период {period}.
Не льсти. Если менеджер работает плохо — так и скажи. Если данных мало — прямо скажи об этом.

================================================================================
ОШИБКИ РАСПОЗНАВАНИЯ РЕЧИ (ASR) — ОБЯЗАТЕЛЬНО К СОБЛЮДЕНИЮ
Диалоги получены автоматической расшифровкой телефонных записей: шум, тихая речь, плохая связь,
смешение казахского и русского. Поэтому имя менеджера и название компании в расшифровке
часто искажены до неузнаваемости или пропущены.
- В первых репликах менеджера имя и компания уже заменены на [имя] и [компания] — это сделано намеренно.
- Все менеджеры работают только в «Эйкос» и представляются своим настоящим именем. Это факт, а не предмет проверки.
- ЗАПРЕЩЕНО делать любые выводы, замечания, пункты коучинга и оценки о том, что менеджер:
  представился чужим / вымышленным / неправильным именем, путает имена, назвал другую или стороннюю
  компанию, нарушил «самоидентификацию». Даже если в тексте звонка встречается чужое имя или название.
- Отсутствие имени или компании в расшифровке тоже НЕ является ошибкой менеджера — их могло «съесть» распознавание.
- Допустимое замечание по началу разговора только одно: менеджер вообще не поздоровался и никак не
  представился (например, ответил «Да?», «Слушаю») — и только если это повторяется минимум в 2 звонках.
- Фразы с искажёнными словами, похожие на ошибку распознавания, не цитируй как доказательство.
================================================================================

ЖЁСТКИЕ ПРАВИЛА:
1. Опирайся ТОЛЬКО на данные ниже. Ничего не выдумывай. Не упоминай звонки, которых нет в данных.
2. В evidence указывай call_id ТОЛЬКО из блоков «ЗВОНОК #…» ниже.
3. quote — ДОСЛОВНАЯ цитата из диалога этого звонка (реплика «М:» или «К:» без изменений), либо null. Не перефразируй.
4. Сравнивай с командой (team_benchmark, team_stage_pass_percent): выделяй, где менеджер хуже ИЛИ лучше среднего по команде.
5. Слабое место — это ПОВТОРЯЮЩИЙСЯ паттерн (минимум 2 звонка) либо критичный единичный случай (конфликт, грубость, отказ помочь клиенту, неверная информация о товаре/цене/наличии).
6. Если звонок — внутренний разговор сотрудников или не разговор с клиентом (см. «Суть»), не делай по нему выводов о работе с клиентами.
7. Не считай «слабостью» этап, который неприменим (not_applicable) — такие звонки уже исключены из процентов.
8. Допродажа не предлагается почти во всех звонках — это системная проблема скрипта/компании. Отметь это отдельно и не делай её единственным выводом: найди то, что отличает ЭТОГО менеджера.
9. Сосредоточься на реальных навыках продаж: выявление потребности, подбор аналогов при отсутствии товара, предложение перемещения шин из другого филиала/города, доведение до брони или продажи, фиксация следующего шага, работа с возражениями, вежливость и удержание клиента.
10. Ответ — СТРОГО валидный JSON без markdown и без ```. Язык — русский.

ФОРМАТ ОТВЕТА:
{
  "verdict": "2-3 предложения: честная общая оценка работы за период",
  "level": "высокий | средний | низкий | критический",
  "weak_points": [
    {
      "title": "короткое название проблемы",
      "severity": "critical | major | minor",
      "pattern": "что именно и как повторяется у менеджера",
      "frequency": "например: в 3 из 8 проблемных звонков",
      "evidence": [{"call_id": 0, "quote": "дословная цитата или null", "comment": "что здесь не так"}],
      "impact": "к чему это приводит (потеря брони, уход клиента и т.п.)",
      "fix": "что конкретно менеджеру делать иначе",
      "sample_phrase": "пример правильной фразы, которую менеджер должен говорить"
    }
  ],
  "strengths": [{"title": "...", "detail": "..."}],
  "missed_opportunities": [{"call_id": 0, "what_missed": "какую возможность упустил (продажа, бронь, услуга, визит)"}],
  "dynamics": "что изменилось за период по данным trend (лучше/хуже/стабильно) или null если данных мало",
  "coaching_plan": [{"focus": "на чём сфокусироваться", "exercise": "как отработать (разбор звонка, ролевая игра и т.п.)"}],
  "data_limitations": "ограничения выборки, которые влияют на достоверность выводов, или null"
}
Слабых мест — от 3 до 5, отсортируй по важности (critical первыми). coaching_plan — не более 3 пунктов.

СВОДНЫЕ МЕТРИКИ (посчитаны кодом, им можно доверять):
{compact_metrics}

САМЫЕ ПРОБЛЕМНЫЕ ЗВОНКИ ПЕРИОДА:
{problem_calls_blocks}
"""


def _build_prompt(manager: str, metrics: dict, selected: List[dict], date_from, date_to) -> str:
    compact = {
        "calls": metrics["calls"],
        "avg_script_percent": metrics["avg_script"],
        "avg_csat": metrics["avg_csat"],
        "avg_politeness": metrics["avg_politeness"],
        "conflicts": metrics["conflicts"],
        "won_rate_percent": metrics["won_rate"],
        "outcomes": metrics["outcomes"],
        "lost_reasons": metrics["lost_reasons"],
        "avg_silence_percent": metrics["avg_silence_pct"],
        "avg_tempo_words_per_min": metrics["avg_tempo"],
        "stages": [{"stage": s["title"], "pass_rate_percent": s["pass_rate"], "failed": s["failed"],
                    "applicable": s["applicable"]} for s in metrics["stages"]],
        "upsell": {k: metrics["upsell"][k] for k in ("applicable", "offered", "missed", "real_upsells")},
        "trend": metrics["trend"]["points"],
        "team_benchmark": {k: v for k, v in (metrics.get("team") or {}).items() if k != "stage_pass"},
        "team_stage_pass_percent": (metrics.get("team") or {}).get("stage_pass"),
    }

    by_id = {c.get("id"): c for c in selected}
    worst_ids = [b["id"] for b in metrics["problem_calls"][:AI_WORST_CALLS]]
    blocks = []
    for cid in worst_ids:
        c = by_id.get(cid)
        if not c:
            continue
        failed = []
        for i in _checklist(c):
            if _item_state(i) == "failed":
                failed.append(f"  - {i.get('title')}: {i.get('desc')}")
        blocks.append(
            f"=== ЗВОНОК #{cid} | {c.get('date')} {c.get('time')} | тема: {c.get('topic')} | "
            f"исход: {c.get('saleStatus')} | скрипт: {c.get('scriptScore')}% | CSAT: {c.get('csat')} | "
            f"тишина: {(c.get('acoustics') or {}).get('silence')} ===\n"
            f"Суть: {c.get('callSummary') or c.get('summary')}\n"
            f"Не выполнено по чек-листу:\n" + ("\n".join(failed) if failed else "  (нет)") + "\n"
            f"Диалог (М — менеджер, К — клиент):\n{_dialogue_text(c)}"
        )

    period = f"{date_from or 'начало'} — {date_to or 'конец'}"
    tmpl = _load_system_prompt_template()
    metrics_json = json.dumps(compact, ensure_ascii=False, indent=1)
    blocks_str = "\n".join(blocks) if blocks else "(проблемных звонков нет)"

    return tmpl.replace("{manager}", manager)\
               .replace("{period}", period)\
               .replace("{compact_metrics}", metrics_json)\
               .replace("{problem_calls_blocks}", blocks_str)


def _load_env_keys():
    """Загружает актуальные переменные окружения из .env файла."""
    env_paths = [
        BASE_DIR / ".env",
        Path("/app/.env"),
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path.cwd() / ".env"
    ]
    for p in env_paths:
        if p.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=p, override=True)
                break
            except Exception:
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, v = line.split("=", 1)
                                k = k.strip()
                                v = v.strip().strip('"').strip("'")
                                if k:
                                    os.environ[k] = v
                    break
                except Exception:
                    pass


def _openrouter_key() -> Optional[str]:
    _load_env_keys()
    val = (os.getenv("OPENROUTER_API_KEY") or "").strip().strip('"').strip("'")
    return val if val else None


def _openrouter_models() -> List[str]:
    env_val = os.getenv("OPENROUTER_MODELS", "") or os.getenv("OPENROUTER_MODEL", "")
    models = [m.strip() for m in env_val.split(",") if m.strip()]
    return models or ["google/gemini-3.7-flash", "google/gemini-2.5-pro", "google/gemini-3.8-flash", "google/gemini-2.5-flash"]


def _gemini_keys() -> List[str]:
    _load_env_keys()
    keys: List[str] = []
    for name in ["GEMINI_API_KEY"] + [f"GEMINI_API_KEY_{i}" for i in range(1, 21)]:
        val = (os.getenv(name) or "").strip().strip('"').strip("'")
        if val and val not in keys:
            keys.append(val)
    return keys


def _gemini_models() -> List[str]:
    env_val = os.getenv("GEMINI_MODELS", "")
    models = [m.strip() for m in env_val.split(",") if m.strip()]
    return models or DEFAULT_GEMINI_MODELS


def _call_openrouter(prompt: str, key: str) -> Optional[Dict[str, str]]:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://aikos.kz",
        "X-Title": "Aikos Manager Analysis"
    }
    for model in _openrouter_models():
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"}
        }
        for attempt in range(3):
            try:
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=body, method="POST", headers=headers)
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                choices = data.get("choices", [])
                if choices:
                    text = choices[0].get("message", {}).get("content", "").strip()
                    if text:
                        logger.info("[Manager AI] Успешный ответ от OpenRouter (%s)", model)
                        return {"text": text, "model": model}
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")[:200]
                logger.warning("[Manager AI OpenRouter] %s: HTTP %s %s", model, e.code, detail)
                if e.code == 400 and "response_format" in detail:
                    payload.pop("response_format", None)
                    continue
                if e.code in (401, 402):
                    raise RuntimeError(f"OpenRouter: ошибка авторизации или баланса ({detail})")
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(2 * (attempt + 1))
                    continue
                break
            except Exception as e:
                logger.warning("[Manager AI OpenRouter] %s: %s", model, e)
                time.sleep(1)
    return None


def _call_gemini(prompt: str) -> Dict[str, str]:
    """Вызов ИИ (OpenRouter или прямой Gemini REST API).
    Автоматически перебирает доступные провайдеры и модели."""
    # 1. Приоритет: OpenRouter API при наличии ключа
    or_key = _openrouter_key()
    if or_key:
        try:
            res = _call_openrouter(prompt, or_key)
            if res:
                return res
        except Exception as ex:
            logger.warning("[Manager AI] Ошибка OpenRouter: %s", ex)
            if not _gemini_keys():
                raise ex

    # 2. Прямой вызов Google Gemini API
    keys = _gemini_keys()
    if not keys and not or_key:
        raise RuntimeError("Не найден API-ключ в окружении. Добавьте OPENROUTER_API_KEY или GEMINI_API_KEY в файл .env.")

    if not keys:
        raise RuntimeError("Не удалось получить ответ от OpenRouter, а прямые ключи GEMINI_API_KEY не указаны.")

    last_error = "неизвестная ошибка"
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }).encode("utf-8")

    for model in _gemini_models():
        for key in keys:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json", "x-goog-api-key": key})
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        payload = json.loads(resp.read().decode("utf-8"))
                    parts = payload["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts).strip()
                    if text:
                        return {"text": text, "model": model}
                    last_error = f"{model}: пустой ответ"
                    break
                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", "ignore")[:200]
                    last_error = f"{model}: HTTP {e.code} {detail}"
                    logger.warning("[Manager AI] %s", last_error)
                    if e.code in (429, 403):
                        break
                    if e.code in (500, 502, 503, 504):
                        time.sleep(2 * (attempt + 1))
                        continue
                    break
                except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
                    last_error = f"{model}: {e}"
                    logger.warning("[Manager AI] %s", last_error)
                    time.sleep(1)
    raise RuntimeError(f"ИИ недоступен: {last_error}")


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _is_asr_greeting_artifact(wp: dict) -> bool:
    """True, если слабое место утверждает, что менеджер представился чужим именем
    или назвал другую компанию. Замечания об ОТСУТСТВИИ приветствия не трогаем."""
    parts = [wp.get("title"), wp.get("pattern"), wp.get("impact"), wp.get("fix")]
    for ev in wp.get("evidence") or []:
        parts += [ev.get("comment"), ev.get("quote")]
    return asr_guard.mentions_identity_mismatch(" ".join(str(p or "") for p in parts))


def _verify_report(report: dict, selected: List[dict]) -> dict:
    """Проверяет ссылки ИИ на реальные звонки и цитаты. Выдуманное — помечаем, а не показываем как факт."""
    by_id = {c.get("id"): c for c in selected}
    haystack = {}
    for cid, c in by_id.items():
        checklist_text = " ".join(str(i.get("desc") or "") for i in _checklist(c))
        masked = " ".join(t for _, t in asr_guard.masked_dialogue_turns(c))
        haystack[cid] = _norm(_full_dialogue_text(c) + " " + masked + " " + checklist_text)

    def fix_evidence(items):
        clean = []
        for ev in items or []:
            try:
                cid = int(ev.get("call_id"))
            except (TypeError, ValueError):
                continue
            if cid not in by_id:
                continue                            # ссылка на несуществующий звонок — выбрасываем
            quote = ev.get("quote")
            ev["call_id"] = cid
            ev["verified"] = bool(quote) and _norm(quote) in haystack[cid]
            if quote and not ev["verified"]:
                ev["quote_unverified"] = True
            clean.append(ev)
        return clean

    clean_wp = []
    removed_asr = 0
    for wp in report.get("weak_points") or []:
        if _is_asr_greeting_artifact(wp):
            removed_asr += 1
            logger.info("[Manager AI] Исключено слабое место (артефакт ASR приветствия/имени): %s", wp.get("title"))
            continue
        wp["evidence"] = fix_evidence(wp.get("evidence"))
        clean_wp.append(wp)
    report["weak_points"] = clean_wp

    # Страховочная сеть: убираем утверждения о «чужом имени / чужой компании»
    for key in ("verdict", "dynamics", "data_limitations"):
        if report.get(key):
            report[key] = asr_guard.drop_mismatch_sentences(report[key]) or None
    for key, fields in (("coaching_plan", ("focus", "exercise")),
                        ("strengths", ("title", "detail"))):
        report[key] = [it for it in (report.get(key) or [])
                       if not asr_guard.mentions_identity_mismatch(" ".join(str(it.get(f) or "") for f in fields))]
    report["asr_filtered"] = removed_asr

    kept = []
    for mo in report.get("missed_opportunities") or []:
        try:
            mo["call_id"] = int(mo.get("call_id"))
        except (TypeError, ValueError):
            continue
        if mo["call_id"] in by_id and not asr_guard.mentions_identity_mismatch(str(mo.get("what_missed") or "")):
            kept.append(mo)
    report["missed_opportunities"] = kept
    return report


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        # храним не более 60 последних отчётов
        if len(cache) > 60:
            for k in sorted(cache, key=lambda k: cache[k].get("generated_at", ""))[:len(cache) - 60]:
                cache.pop(k, None)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning("[Manager AI] Не удалось записать кэш: %s", e)


def run_ai_analysis(all_calls: List[dict], manager: str, date_from: Optional[date],
                    date_to: Optional[date], refresh: bool = False) -> dict:
    all_calls = asr_guard.normalize_calls(all_calls)
    selected = filter_calls(all_calls, manager, date_from, date_to)
    if not selected:
        raise ValueError("За выбранный период у менеджера нет звонков.")
    team = filter_calls(all_calls, None, date_from, date_to)
    metrics = build_metrics(selected, team, date_from, date_to)

    key = _fingerprint(manager, date_from, date_to, selected)
    cache = _load_cache()
    if not refresh and key in cache:
        cached = dict(cache[key])
        if "report" in cached:
            cached["report"] = _verify_report(cached["report"], selected)
        cached["cached"] = True
        return cached

    prompt = _build_prompt(manager, metrics, selected, date_from, date_to)
    raw = _call_gemini(prompt)
    try:
        report = _parse_json(raw["text"])
    except (ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f"ИИ вернул некорректный JSON: {e}")
    report = _verify_report(report, selected)

    result = {
        "manager": manager,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "calls_analyzed": len(selected),
        "calls_with_full_dialogue": min(AI_WORST_CALLS, len(metrics["problem_calls"])),
        "model": raw["model"],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cached": False,
        "report": report,
    }
    cache[key] = result
    _save_cache(cache)
    return result
