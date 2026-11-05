# -*- coding: utf-8 -*-
"""
whisper_test.py — расшифровка звонков одной моделью Whisper, отдельно от воркеров.

Звонки кладутся вручную в эту же папку (wishper/), запуск отсюда же. Рядом с каждым звонком появляется TXT:
  python whisper_test.py ru   ->  <звонок>.ru.txt  (large-v3-turbo, русский)
  python whisper_test.py kk   ->  <звонок>.kk.txt  (казахская модель test/models/kazakh-turbo-int8)

База не трогается, аудио не перемещается. Звонки, для которых TXT этой модели уже есть, пропускаются;
чтобы расшифровать заново, удалите TXT.
"""

import os
import sys
import time

FOLDER = os.path.dirname(os.path.abspath(__file__))   # звонки и TXT лежат рядом со скриптом
sys.path.insert(0, os.path.dirname(FOLDER))            # local_pipeline.py — в корне проекта

import local_pipeline as lp
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def settings_for(mode):
    base = lp.asr_settings()
    if mode == "ru":
        return {**base, "language": "ru"}
    kk = lp.kazakh_settings(base)
    if not kk:
        sys.exit("Казахская модель не найдена, см. ASR_MODEL_KK")
    return kk


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("ru", "kk"):
        sys.exit("Запуск: python whisper_test.py ru   или   python whisper_test.py kk")
    mode = sys.argv[1]
    settings = settings_for(mode)
    model_name = os.path.basename(settings["model"])

    files = sorted(n for n in os.listdir(FOLDER) if n.lower().endswith(AUDIO_EXTENSIONS))
    todo = [n for n in files if not os.path.exists(os.path.join(FOLDER, f"{os.path.splitext(n)[0]}.{mode}.txt"))]
    if not todo:
        sys.exit(f"В {FOLDER} нет новых звонков для модели {mode} (всего аудио: {len(files)})")

    print(f"Модель {model_name}, звонков {len(todo)} (пропущено уже готовых: {len(files) - len(todo)})")
    started = time.time()
    lp.get_asr_model(settings)
    print(f"Модель загружена за {time.time() - started:.1f} с")

    for index, name in enumerate(todo, 1):
        stem = os.path.splitext(name)[0]
        print(f"[{index}/{len(todo)}] {name} ...", flush=True)
        asr = lp.transcribe(os.path.join(FOLDER, name), settings)
        speed = asr["duration"] / asr["seconds"] if asr["seconds"] else 0
        header = (f"# Модель: {model_name}, язык: {settings['language']}\n"
                  f"# Аудио {asr['duration']:.0f} с, расшифровка {asr['seconds']:.1f} с (×{speed:.1f}), "
                  f"реплик {len(asr['segments'])}\n\n")
        body = "\n".join(f"[{s['start']:.1f}] {s['text']}" for s in asr["segments"]) or "(речь не найдена)"
        with open(os.path.join(FOLDER, f"{stem}.{mode}.txt"), "w", encoding="utf-8") as f:
            f.write(header + body + "\n")
        print(f"[{index}/{len(todo)}] готово: аудио {asr['duration']:.0f} с за {asr['seconds']:.1f} с (×{speed:.1f})")

    print(f"Всё готово за {time.time() - started:.0f} с. TXT лежат в {FOLDER}")


if __name__ == "__main__":
    main()
