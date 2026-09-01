import os
import sys

# Сбрасываем возможные унаследованные переменные окружения, мешающие загрузке
os.environ.pop("HF_ENDPOINT", None)

# Входной тестовый аудиофайл из папки incoming
audio_path = r"incoming/rg-5555-87014662990-20201113-150553-1605258353.53425.wav"

def analyze_audio_emotion(file_path):
    if not os.path.exists(file_path):
        print(f"Файл {file_path} не найден. Пожалуйста, положите тестовый WAV-файл в папку incoming.")
        return

    try:
        print("Импорт библиотек librosa, soundfile, torch, transformers...")
        import torch
        import librosa
        import numpy as np
        from transformers import Wav2Vec2ForSequenceClassification, Wav2Vec2FeatureExtractor
    except ImportError:
        print("\nНеобходимые библиотеки не установлены. Установите их командой:")
        print("uv add transformers torch librosa soundfile")
        return

    print("Загрузка модели Wav2Vec2 Speech Emotion Recognition с Hugging Face...")
    # Официальная модель классификации эмоций по аудио на базе Wav2Vec2 от SUPERB
    model_name = "superb/wav2vec2-base-superb-er"
    
    try:
        feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(model_name)
        model = Wav2Vec2ForSequenceClassification.from_pretrained(model_name, use_safetensors=False)
    except Exception as e:
        print(f"Ошибка загрузки модели: {e}")
        return

    print(f"Загрузка и предобработка аудиофайла {os.path.basename(file_path)}...")
    try:
        # Wav2Vec2 ожидает частоту дискретизации 16кГц (16000 Hz)
        speech, sr = librosa.load(file_path, sr=16000)
    except Exception as e:
        print(f"Не удалось загрузить аудиофайл: {e}")
        return

    print("Анализ интонаций и голоса на эмоции...")
    inputs = feature_extractor(speech, sampling_rate=16000, return_tensors="pt", padding=True)
    
    # Модель возвращает логиты для каждого класса эмоций
    # Динамически получаем метки классов из конфигурации модели
    id2label = model.config.id2label
    # Словарь перевода меток на русский язык
    label_translation = {
        "neu": "Нейтральный (Neutral)",
        "ang": "Гнев (Angry)",
        "sad": "Грусть (Sad)",
        "hap": "Радость (Happy)"
    }
    emotions_labels = [label_translation.get(id2label[i], id2label[i]) for i in range(len(id2label))]
    
    with torch.no_grad():
        logits = model(**inputs).logits
        
    scores = torch.softmax(logits, dim=-1).numpy()[0]
    
    print("\n=== РЕЗУЛЬТАТ АНАЛИЗА ЗВУКА НА ЭМОЦИИ ===")
    for emotion, score in zip(emotions_labels, scores):
        print(f"{emotion}: {score:.2%}")

    # Сохраняем в md
    output_dir = ".tech"
    os.makedirs(output_dir, exist_ok=True)
    audio_basename = os.path.basename(file_path)
    output_path = os.path.join(output_dir, f"{audio_basename}_local_emotion.md")

    md_content = f"# Результат анализа звука на эмоции (Wav2Vec2)\n\n"
    md_content += f"* **Аудиофайл**: `{audio_basename}`\n\n"
    md_content += "| Эмоция | Вероятность |\n| :--- | :--- |\n"
    for emotion, score in zip(emotions_labels, scores):
        md_content += f"| {emotion} | {score:.2%} |\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"\n[Успешно] Результат сохранен в: {output_path}")

if __name__ == "__main__":
    analyze_audio_emotion(audio_path)
