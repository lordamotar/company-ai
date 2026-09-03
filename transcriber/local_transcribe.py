import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from faster_whisper import WhisperModel


def transcribe_file(
    input_file,
    output_file,
    model_size="base"
):
    print()
    print("=" * 60)
    print("LOCAL TRANSCRIBER")
    print("=" * 60)

    print(f"Файл: {input_file}")
    print(f"Модель: {model_size}")
    print()

    if not os.path.exists(input_file):
        raise FileNotFoundError(
            f"Файл не найден: {input_file}"
        )

    print("Загрузка модели...")

    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8"
    )

    print("Модель загружена.")
    print("Начинаем транскрибацию...")
    print()

    segments, info = model.transcribe(
        input_file,
        beam_size=5
    )

    text_parts = []

    for segment in segments:

        text = segment.text.strip()

        if text:
            text_parts.append(text)

            print(
                f"[{segment.start:.1f}s - "
                f"{segment.end:.1f}s] "
                f"{text}"
            )

    full_text = " ".join(text_parts)

    os.makedirs(
        os.path.dirname(output_file),
        exist_ok=True
    )

    with open(
        output_file,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(full_text)

    print()
    print("=" * 60)
    print("ГОТОВО")
    print("=" * 60)
    print(f"TXT: {output_file}")

    return full_text


if __name__ == "__main__":

    if len(sys.argv) < 3:

        print(
            "Использование:"
        )

        print(
            "uv run python local_transcribe.py "
            "input.mp3 output.txt"
        )

        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    transcribe_file(
        input_file,
        output_file
    )
