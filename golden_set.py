#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
golden_set.py — эталонный набор звонков (golden set) и сравнение моделей.

Идея: РОП вручную проверяет 100–150 звонков и фиксирует «правильные ответы».
Затем любые модели и версии промпта (Gemini, OpenRouter, в будущем локальная) прогоняются
по этим звонкам, и скрипт считает, насколько их ответы совпадают с разметкой РОПа.

Команды:
  python golden_set.py template 101 102 1789725452.1392 [--out golden_template.csv]
      Выгружает CSV для Excel, заполненный текущими значениями звонков (с учётом правок РОПа в интерфейсе).
      Аргументы — id звонка (число) или unique_id (с точкой).
      Без аргументов: --last N  — последние N обработанных звонков.

  python golden_set.py import golden_template.csv [--by "Имя РОПа"]
      РОП исправляет значения в CSV, ставит «да» в колонке «проверено», сохраняет.
      В эталон попадают только строки с «проверено = да».

  python golden_set.py list
  python golden_set.py remove 1789725452.1392 [...]

  python golden_set.py rerun --provider gemini|openrouter [--model ИМЯ] [--limit N] [--delay СЕК] [--force]
      Прогоняет эталонные звонки через модель и сохраняет результат как проверочный прогон
      (purpose='eval'). Боевые данные звонков НЕ меняются.

  python golden_set.py report [--details]
      Точность каждой связки «провайдер / модель / версия промпта» относительно эталона.

Требует миграции: python apply_migrations.py
"""

import os
import sys
import csv
import json
import time
import argparse
import logging
import importlib.util
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "company"),
    "user": os.getenv("DB_USER", "company_user"),
    "password": os.getenv("DB_PASSWORD", "company_password"),
}

# (ключ эталона, путь в JSON-ответе модели, тип сравнения, заголовок колонки CSV)
FIELDS = [
    ("sale_status", "commercial.sale_status", "text", "итог звонка"),
    ("call_category", "commercial.call_category", "text", "категория"),
    ("call_subcategory", "commercial.call_subcategory", "text", "подкатегория"),
    ("tire_brand", "commercial.tire_brand", "brand", "бренд шин"),
    ("tire_size", "commercial.tire_size", "size", "типоразмер"),
    ("tire_season", "commercial.tire_season", "text", "сезон"),
    ("city_name", "commercial.city_name", "text", "город"),
    ("satisfaction_score", "overall_sentiment.satisfaction_score", "score", "CSAT (1-5)"),
    ("manager_politeness", "overall_sentiment.manager_politeness", "score", "вежливость (1-5)"),
    ("conflict_detected", "overall_sentiment.conflict_detected", "bool", "конфликт (да/нет)"),
]

CHECKLIST_ITEMS = {
    1: "Приветствие",
    2: "Выявление потребностей",
    3: "Презентация",
    4: "Возражения",
    5: "Допродажа",
    6: "Проф. подход",
    7: "Окончание разговора",
}

STATUS_TO_RU = {"passed": "пройден", "failed": "не пройден", "not_applicable": "неприменимо"}
RU_TO_STATUS = {
    "пройден": "passed", "да": "passed", "+": "passed", "passed": "passed",
    "не пройден": "failed", "нет": "failed", "-": "failed", "failed": "failed",
    "неприменимо": "not_applicable", "н/п": "not_applicable", "не применимо": "not_applicable",
    "not_applicable": "not_applicable",
}
YES = {"да", "+", "1", "yes", "y", "x", "х", "true"}
EMPTY = {"", "none", "null", "-", "—", "не указано", "не определено", "не определен"}


# ----------------------------------------------------------------- нормализация

def _norm_text(v):
    if v is None:
        return ""
    s = str(v).strip().lower().replace("ё", "е")
    s = " ".join(s.split())
    s = s.strip(" .,;:«»\"'")
    return "" if s in EMPTY else s


def normalize(value, kind):
    if kind == "score":
        try:
            n = int(round(float(str(value).replace(",", "."))))
            return n if 1 <= n <= 5 else None
        except (TypeError, ValueError):
            return None
    if kind == "bool":
        if isinstance(value, bool):
            return value
        s = _norm_text(value)
        if s in ("да", "true", "1", "yes", "есть"):
            return True
        if s in ("нет", "false", "0", "no", ""):
            return False
        return None
    s = _norm_text(value)
    if kind == "brand":
        return s.replace(" ", "").replace("-", "")
    if kind == "size":
        return s.upper().replace(" ", "").replace("Р", "R")
    return s


def checklist_status(item):
    st = str(item.get("status") or "").strip().lower()
    if st in ("passed", "failed", "not_applicable"):
        return st
    passed = item.get("passed")
    if passed is True:
        return "passed"
    if passed is False:
        return "failed"
    return "not_applicable"


def checklist_map(checklist):
    """{id: status} из чек-листа модели или БД."""
    if isinstance(checklist, str):
        try:
            checklist = json.loads(checklist)
        except Exception:
            checklist = []
    result = {}
    for idx, item in enumerate(checklist or [], start=1):
        if not isinstance(item, dict):
            continue
        try:
            cid = int(item.get("id") or idx)
        except (TypeError, ValueError):
            cid = idx
        result[cid] = checklist_status(item)
    return result


def get_path(data, path):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def predictions_from_result(result):
    """Достаёт из JSON-ответа модели значения, сопоставимые с эталоном."""
    pred = {key: get_path(result or {}, path) for key, path, _, _ in FIELDS}
    pred["checklist"] = checklist_map((result or {}).get("checklist"))
    return pred


def compare(labels, pred):
    """Возвращает {поле: True/False}; поля без разметки пропускаются."""
    out = {}
    for key, _, kind, _ in FIELDS:
        if key not in labels:
            continue
        g = normalize(labels[key], kind)
        p = normalize(pred.get(key), kind)
        if kind == "score":
            if g is None:
                continue
            out[key] = (p == g)
            out[key + "±1"] = (p is not None and abs(p - g) <= 1)
        else:
            out[key] = (g == p)
    g_chk = labels.get("checklist") or {}
    p_chk = pred.get("checklist") or {}
    for cid, g_status in g_chk.items():
        out[f"чек-лист {cid}. {CHECKLIST_ITEMS.get(int(cid), '')}".strip()] = (p_chk.get(int(cid)) == g_status)
    return out


# ----------------------------------------------------------------- база

def db():
    return psycopg2.connect(**DB_CONFIG)


def fetch_calls(identifiers=None, last=None):
    sql = """
        SELECT t.id, t.unique_id, t.source_file, t.call_date, t.manager_name, t.call_summary,
               t.sale_status, t.call_category, t.call_subcategory, t.tire_brand, t.tire_size, t.tire_season,
               t.city_name, cs.satisfaction_score, cs.manager_politeness, cs.conflict_detected, t.checklist
        FROM call_transcriptions t
        LEFT JOIN customer_sentiment cs ON cs.call_id = t.id
    """
    params = ()
    if identifiers:
        ids = [int(x) for x in identifiers if x.isdigit()]
        uids = [x for x in identifiers if not x.isdigit()]
        sql += " WHERE t.id = ANY(%s) OR t.unique_id = ANY(%s) ORDER BY t.id"
        params = (ids, uids)
    else:
        sql += " WHERE t.status = 'completed' ORDER BY t.id DESC LIMIT %s"
        params = (last or 50,)
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# ----------------------------------------------------------------- команды

def cmd_template(args):
    if not args.ids and not args.last:
        sys.exit("Укажите id/unique_id звонков или --last N")
    rows = fetch_calls(args.ids, args.last)
    if not rows:
        sys.exit("Звонки не найдены.")
    headers = ["проверено", "unique_id", "id", "дата", "менеджер", "файл", "резюме"]
    headers += [h for _, _, _, h in FIELDS]
    headers += [f"чек-лист {cid}. {name}" for cid, name in CHECKLIST_ITEMS.items()]
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(headers)
        for r in rows:
            chk = checklist_map(r.get("checklist"))
            line = ["", r["unique_id"], r["id"], r.get("call_date") or "", r.get("manager_name") or "",
                    r.get("source_file") or "", (r.get("call_summary") or "")[:300]]
            for key, _, kind, _ in FIELDS:
                v = r.get(key)
                if kind == "bool":
                    v = "да" if v else "нет"
                line.append("" if v is None else v)
            line += [STATUS_TO_RU.get(chk.get(cid), "") for cid in CHECKLIST_ITEMS]
            w.writerow(line)
    print(f"Шаблон на {len(rows)} звонков сохранён: {args.out}")
    print("Дальше: РОП прослушивает звонок, исправляет значения, ставит «да» в колонке «проверено»,")
    print(f"сохраняет файл и запускает: python golden_set.py import {args.out} --by \"Имя РОПа\"")
    print("Важно: CSAT, вежливость, конфликт и чек-лист сейчас предзаполнены ответом модели —")
    print("их нужно именно проверить, иначе эталон будет повторять модель.")


def cmd_import(args):
    with open(args.file, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delim = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.DictReader(f, delimiter=delim)
        saved, skipped = 0, 0
        with db() as conn, conn.cursor() as cur:
            for row in reader:
                if _norm_text(row.get("проверено")) not in YES:
                    skipped += 1
                    continue
                uid = (row.get("unique_id") or "").strip()
                if not uid:
                    skipped += 1
                    continue
                labels = {}
                for key, _, kind, header in FIELDS:
                    raw = row.get(header)
                    if raw is None:
                        continue
                    labels[key] = normalize(raw, kind) if kind in ("score", "bool") else raw.strip()
                chk = {}
                for cid, name in CHECKLIST_ITEMS.items():
                    raw = _norm_text(row.get(f"чек-лист {cid}. {name}"))
                    if raw in RU_TO_STATUS:
                        chk[cid] = RU_TO_STATUS[raw]
                    elif raw:
                        print(f"[!] {uid}: непонятный статус чек-листа {cid} «{raw}» — пункт не учтён "
                              f"(допустимо: пройден / не пройден / неприменимо).")
                if chk:
                    labels["checklist"] = chk
                cur.execute("""
                    INSERT INTO golden_calls (unique_id, labels, verified_by, verified_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (unique_id) DO UPDATE
                    SET labels = EXCLUDED.labels, verified_by = EXCLUDED.verified_by, verified_at = now();
                """, (uid, json.dumps(labels, ensure_ascii=False), args.by))
                saved += 1
            conn.commit()
    print(f"В эталон сохранено: {saved}. Пропущено (не отмечены «проверено = да»): {skipped}.")


def cmd_list(args):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT unique_id, verified_by, verified_at FROM golden_calls ORDER BY verified_at;")
        rows = cur.fetchall()
    for uid, by, at in rows:
        print(f"{uid:28} {by or '':20} {at:%Y-%m-%d %H:%M}")
    print(f"Всего в эталоне: {len(rows)} звонков.")


def cmd_remove(args):
    with db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM golden_calls WHERE unique_id = ANY(%s);", (args.ids,))
        n = cur.rowcount
        conn.commit()
    print(f"Удалено из эталона: {n}.")


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cmd_rerun(args):
    from analysis_runs import record_run, prompt_version
    from app.services.audio_service import find_audio_file

    if args.provider == "gemini":
        mod = _load_module("worker", "worker.py")
        analyze = mod.transcribe_and_analyze_with_gemini
    elif args.provider == "openrouter":
        mod = _load_module("worker_openrouter", "worker-openrouter.py")
        analyze = mod.transcribe_and_analyze_with_openrouter
    else:
        sys.exit("Провайдер 'local' появится вместе с локальным пайплайном (см. docs/LOCAL_SERVER.md).")

    if args.model:
        mod.get_configured_models = lambda: [args.model]
    pv = prompt_version(mod.PROMPT_UNIFIED_ANALYSIS)

    with db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT g.unique_id, t.id, t.source_file, t.phone
            FROM golden_calls g
            LEFT JOIN call_transcriptions t ON t.unique_id = g.unique_id
            ORDER BY g.verified_at;
        """)
        golden = cur.fetchall()
        cur.execute("""
            SELECT DISTINCT unique_id FROM call_analysis_runs
            WHERE provider = %s AND prompt_version = %s AND (%s IS NULL OR model = %s);
        """, (args.provider, pv, args.model, args.model))
        done = {r[0] for r in cur.fetchall()}

    todo = [g for g in golden if args.force or g[0] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"Эталон: {len(golden)} звонков, уже прогнано этой версией: {len(done & {g[0] for g in golden})}, к прогону: {len(todo)}.")
    print(f"Провайдер: {args.provider}, модель: {args.model or 'по умолчанию из .env'}, промпт: {pv}")

    ok = 0
    for i, (uid, call_id, source_file, phone) in enumerate(todo, start=1):
        audio = find_audio_file(call_id=call_id, source_file=source_file, unique_id=uid, phone=phone)
        if not audio or not os.path.isfile(audio):
            print(f"[{i}/{len(todo)}] {uid}: аудиофайл не найден, пропуск.")
            continue
        meta = {}
        t0 = time.time()
        try:
            result, _text = analyze(audio, meta_out=meta)
        except Exception as e:
            print(f"[{i}/{len(todo)}] {uid}: ошибка анализа: {str(e)[:200]}")
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print("Квота исчерпана, останавливаюсь. Запустите позже — уже прогнанные звонки повторно не пойдут.")
                break
            continue
        record_run(uid, meta.get("provider", args.provider), meta.get("model"), meta.get("prompt_version", pv),
                   result, call_id=call_id, duration_ms=int((time.time() - t0) * 1000),
                   purpose="eval", update_call=False)
        ok += 1
        print(f"[{i}/{len(todo)}] {uid}: готово ({meta.get('model')}).")
        if i < len(todo):
            time.sleep(args.delay)
    print(f"Проверочных прогонов записано: {ok}. Отчёт: python golden_set.py report")


def cmd_report(args):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT unique_id, labels FROM golden_calls;")
        golden = {uid: (lbl if isinstance(lbl, dict) else json.loads(lbl)) for uid, lbl in cur.fetchall()}
        if not golden:
            sys.exit("Эталон пуст. Сначала: golden_set.py template … и golden_set.py import …")
        cur.execute("""
            SELECT unique_id, provider, model, prompt_version, result, created_at
            FROM call_analysis_runs
            WHERE unique_id = ANY(%s) AND result IS NOT NULL
            ORDER BY created_at;
        """, (list(golden.keys()),))
        runs = cur.fetchall()

    # последний прогон каждой связки по каждому звонку
    latest = defaultdict(dict)
    for uid, provider, model, pv, result, _ in runs:
        latest[(provider, model or "?", pv or "?")][uid] = result if isinstance(result, dict) else json.loads(result)

    print("=" * 78)
    print(f"ТОЧНОСТЬ ОТНОСИТЕЛЬНО ЭТАЛОНА РОПа  (звонков в эталоне: {len(golden)})")
    print("=" * 78)
    if not latest:
        print("Прогонов по эталонным звонкам нет. Запустите: python golden_set.py rerun --provider gemini")
        return

    for (provider, model, pv), by_uid in sorted(latest.items()):
        stats = defaultdict(lambda: [0, 0])
        misses = defaultdict(list)
        for uid, result in by_uid.items():
            pred = predictions_from_result(result)
            for field, hit in compare(golden[uid], pred).items():
                stats[field][1] += 1
                stats[field][0] += int(hit)
                if not hit:
                    misses[field].append(uid)
        total_hit = sum(v[0] for k, v in stats.items() if not k.endswith("±1"))
        total_n = sum(v[1] for k, v in stats.items() if not k.endswith("±1"))
        overall = round(100 * total_hit / total_n, 1) if total_n else 0
        print(f"\n▶ {provider} / {model} / промпт {pv}   звонков: {len(by_uid)}   общая точность: {overall}%")
        for field, (hit, n) in stats.items():
            pct = round(100 * hit / n, 1) if n else 0
            print(f"   {field:38} {pct:6.1f}%   ({hit}/{n})")
            if args.details and misses.get(field):
                print(f"      промахи: {', '.join(misses[field][:15])}")
    print("\nСравнивайте связки только на одинаковом наборе звонков (колонка «звонков»).")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Эталонный набор звонков и сравнение моделей")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("template", help="выгрузить CSV для разметки РОПом")
    p.add_argument("ids", nargs="*")
    p.add_argument("--last", type=int)
    p.add_argument("--out", default="golden_template.csv")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("import", help="загрузить проверенный CSV в эталон")
    p.add_argument("file")
    p.add_argument("--by", default="РОП")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("list", help="список эталонных звонков")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="убрать звонки из эталона")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("rerun", help="прогнать эталон через модель (боевые данные не меняются)")
    p.add_argument("--provider", required=True, choices=["gemini", "openrouter", "local"])
    p.add_argument("--model")
    p.add_argument("--limit", type=int)
    p.add_argument("--delay", type=float, default=5.0)
    p.add_argument("--force", action="store_true", help="прогнать повторно даже уже прогнанные")
    p.set_defaults(func=cmd_rerun)

    p = sub.add_parser("report", help="отчёт о точности")
    p.add_argument("--details", action="store_true")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
