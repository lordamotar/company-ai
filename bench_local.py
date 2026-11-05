# -*- coding: utf-8 -*-
"""
bench_local.py — замер локальных моделей на записях из incoming/.

Базу не трогает, аудио не перемещает и не удаляет. Результаты с расшифровками лежат в
test/ollama_bench/ (папка в .gitignore: там персональные данные).

  python bench_local.py asr --model large-v3-turbo [--compute int8] [--device cuda] [--limit N]
  python bench_local.py llm --model qwen3:4b --asr-run <папка прогона ASR> [--limit N]
  python bench_local.py summary

Ход работы выводится на экран и пишется в test/ollama_bench/bench.log.
Звонки идут от самых длинных к самым коротким.
"""

import argparse
import glob
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time

import local_pipeline as lp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Только incoming/: звонки кладут сюда вручную, папку call/ скрипт не читает
INCOMING_DIR = os.path.join(BASE_DIR, "incoming")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma")
OUT_DIR = os.path.join(BASE_DIR, "test", "ollama_bench")

os.makedirs(OUT_DIR, exist_ok=True)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(OUT_DIR, "bench.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)


def mmss(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 \
        else f"{seconds // 60:02d}:{seconds % 60:02d}"


class VramPeak:
    """Пик занятой видеопамяти в МБ за время блока, опрос nvidia-smi."""

    def __enter__(self):
        self.peak = self.base = self._used()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()

    def _used(self):
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True)
        return int(out.stdout.strip().splitlines()[0])

    def _poll(self):
        while not self._stop.wait(0.5):
            self.peak = max(self.peak, self._used())


def slug(text):
    return re.sub(r"[^\w.-]+", "_", os.path.basename(text.rstrip("/\\")))


def calls(limit):
    files = [os.path.join(INCOMING_DIR, name) for name in os.listdir(INCOMING_DIR)
             if name.lower().endswith(AUDIO_EXTENSIONS)]
    if not files:
        sys.exit(f"В папке {INCOMING_DIR} нет звонков. Перенесите нужные файлы и запустите снова.")
    files.sort(key=os.path.getsize, reverse=True)
    # Берём равномерно по длине, чтобы короткий прогон не состоял только из длинных звонков
    if limit and limit < len(files):
        step = len(files) / limit
        files = [files[int(i * step)] for i in range(limit)]
    return files


def cmd_asr(args):
    settings = {"model": args.model, "device": args.device, "compute_type": args.compute,
                "language": args.language, "beam_size": args.beam}
    name = slug(f"asr_{args.model}_{args.device}_{args.compute}_b{args.beam}" + (f"_{args.language}" if args.language else ""))
    run_dir = os.path.join(OUT_DIR, name)
    os.makedirs(run_dir, exist_ok=True)

    files = calls(args.limit)
    total_bytes = sum(os.path.getsize(p) for p in files)
    logging.info("=" * 60)
    logging.info(f"СТАРТ распознавания: {args.model}, {args.compute}, звонков {len(files)}, "
                 f"{total_bytes / 1024 / 1024:.1f} МБ")
    logging.info(f"Загрузка модели {args.model} (при первом запуске она ещё скачивается)...")

    rows = []
    done_bytes = 0
    with VramPeak() as vram:
        started = time.time()
        lp.get_asr_model(settings)
        load_sec = round(time.time() - started, 1)
        logging.info(f"Модель загружена за {load_sec} с")
        work_started = time.time()
        for index, path in enumerate(files, 1):
            stem = os.path.splitext(os.path.basename(path))[0]
            logging.info(f"[{index}/{len(files)}] Начало: {stem}")
            asr = lp.transcribe(path, settings)
            with open(os.path.join(run_dir, stem + ".json"), "w", encoding="utf-8") as f:
                json.dump(asr, f, ensure_ascii=False, indent=1)
            rows.append({
                "call": stem, "audio_sec": asr["duration"], "asr_sec": asr["seconds"],
                "speed_x": round(asr["duration"] / asr["seconds"], 1) if asr["seconds"] else 0,
                "language": asr["language"], "lang_prob": asr["language_probability"],
                "segments": len(asr["segments"]), "words": sum(len(s["text"].split()) for s in asr["segments"]),
            })
            row = rows[-1]
            done_bytes += os.path.getsize(path)
            elapsed = time.time() - work_started
            left = elapsed / done_bytes * (total_bytes - done_bytes)
            logging.info(f"[{index}/{len(files)}] Готово: аудио {mmss(row['audio_sec'])}, обработка {row['asr_sec']} с "
                         f"(×{row['speed_x']}), язык {row['language']}, слов {row['words']}. "
                         f"Прошло {mmss(elapsed)}, осталось примерно {mmss(left)}")
            if not row["segments"]:
                logging.warning(f"[{index}/{len(files)}] Речь не найдена: {stem}")

    audio, spent = sum(r["audio_sec"] for r in rows), sum(r["asr_sec"] for r in rows)
    summary = {"run": name, "calls": len(rows), "audio_min": round(audio / 60, 1), "asr_min": round(spent / 60, 1),
               "speed_x": round(audio / spent, 1) if spent else 0, "load_sec": load_sec,
               "vram_base_mb": vram.base, "vram_peak_mb": vram.peak}
    with open(os.path.join(run_dir, "_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    logging.info("=" * 60)
    logging.info(f"[✓ ГОТОВО] Распознано {len(rows)} звонков: {summary['audio_min']} мин аудио "
                 f"за {summary['asr_min']} мин (×{summary['speed_x']}), пик видеопамяти {vram.peak} МБ")
    logging.info(f"Результаты: {run_dir}")
    logging.info("=" * 60)


def cmd_llm(args):
    asr_dir = os.path.join(OUT_DIR, args.asr_run)
    run_dir = os.path.join(OUT_DIR, slug(f"llm_{args.model}_ctx{lp.OLLAMA_NUM_CTX}"))
    os.makedirs(run_dir, exist_ok=True)
    files = sorted(f for f in glob.glob(os.path.join(asr_dir, "*.json")) if not os.path.basename(f).startswith("_"))
    files.sort(key=os.path.getsize, reverse=True)
    if args.limit and args.limit < len(files):
        step = len(files) / args.limit
        files = [files[int(i * step)] for i in range(args.limit)]

    logging.info("=" * 60)
    logging.info(f"СТАРТ анализа: {args.model}, контекст {lp.OLLAMA_NUM_CTX}, звонков {len(files)}. "
                 f"Расшифровки: {args.asr_run}")

    rows = []
    with VramPeak() as vram:
        for index, path in enumerate(files, 1):
            stem = os.path.splitext(os.path.basename(path))[0]
            with open(path, encoding="utf-8") as f:
                asr = json.load(f)
            if not asr["segments"]:
                logging.warning(f"[{index}/{len(files)}] Пропуск, в расшифровке нет речи: {stem}")
                continue
            logging.info(f"[{index}/{len(files)}] Начало: {stem}, реплик {len(asr['segments'])}")
            row = {"call": stem, "audio_sec": asr["duration"], "segments": len(asr["segments"])}
            started = time.time()
            try:
                llm, stats = lp.analyze_transcript(asr["segments"], args.model)
                result = lp.merge_result(asr, llm)
                problems = lp.validate_result(result, len(asr["segments"]))
                labelled = len({l.get("i") for l in llm.get("lines", []) if isinstance(l, dict)})
                row.update(stats)
                row.update(wall_sec=round(time.time() - started, 1), error="; ".join(problems),
                           lines_labelled=labelled)
                with open(os.path.join(run_dir, stem + ".json"), "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=1)
            except Exception as e:
                row.update(wall_sec=round(time.time() - started, 1), error=str(e)[:200])
            rows.append(row)
            if row.get("error"):
                logging.error(f"[{index}/{len(files)}] Ошибка за {mmss(row['wall_sec'])}: {row['error']}")
            else:
                logging.info(f"[{index}/{len(files)}] Готово за {mmss(row['wall_sec'])}: "
                             f"запрос {row['prompt_tokens']} ток., ответ {row['output_tokens']} ток., "
                             f"{row['tokens_per_sec']} ток/с, размечено реплик {row['lines_labelled']}/{row['segments']}")

    ok =[r for r in rows if not r.get("error")]
    spent = sum(r["wall_sec"] for r in rows)
    audio = sum(r["audio_sec"] for r in rows)
    summary = {"run": os.path.basename(run_dir), "calls": len(rows), "ok": len(ok), "llm_min": round(spent / 60, 1),
               "speed_x": round(audio / spent, 2) if spent else 0, "avg_sec_per_call": round(spent / max(1, len(rows)), 1),
               "avg_tok_per_sec": round(sum(r.get("tokens_per_sec") or 0 for r in ok) / max(1, len(ok)), 1),
               "avg_prompt_tokens": round(sum(r.get("prompt_tokens") or 0 for r in ok) / max(1, len(ok))),
               "vram_base_mb": vram.base, "vram_peak_mb": vram.peak}
    with open(os.path.join(run_dir, "_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    logging.info("=" * 60)
    logging.info(f"[✓ ГОТОВО] Анализ: успешно {len(ok)} из {len(rows)}, всего {mmss(spent)}, "
                 f"в среднем {mmss(summary['avg_sec_per_call'])} на звонок")
    logging.info(f"Результаты: {run_dir}")
    logging.info("=" * 60)


def cmd_summary(_):
    for path in sorted(glob.glob(os.path.join(OUT_DIR, "*", "_summary.json"))):
        with open(path, encoding="utf-8") as f:
            print(json.load(f))


def main():
    parser = argparse.ArgumentParser(description="Замер локальных моделей на звонках из incoming/")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("asr")
    p.add_argument("--model", required=True)
    p.add_argument("--device", default="cuda")
    p.add_argument("--compute", default="int8")
    p.add_argument("--language")
    p.add_argument("--beam", type=int, default=5)
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_asr)
    p = sub.add_parser("llm")
    p.add_argument("--model", required=True)
    p.add_argument("--asr-run", required=True)
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_llm)
    sub.add_parser("summary").set_defaults(func=cmd_summary)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
