import os
import sys

# Сбрасываем возможные унаследованные переменные окружения, мешающие загрузке
os.environ.pop("HF_ENDPOINT", None)

# Входной тестовый аудиофайл из папки incoming
audio_path = r"incoming/rg-5555-87014662990-20201113-150553-1605258353.53425.wav"

def analyze_audio_segments(file_path):
    if not os.path.exists(file_path):
        print(f"Файл {file_path} не найден.")
        return

    try:
        import torch
        import librosa
        import numpy as np
        from transformers import Wav2Vec2ForSequenceClassification, Wav2Vec2FeatureExtractor
    except ImportError:
        print("\nБиблиотеки не установлены. Установите их командой:")
        print("uv add transformers torch librosa soundfile")
        return

    print("Загрузка модели Wav2Vec2 SER с Hugging Face...")
    # Официальная модель классификации эмоций по аудио на базе Wav2Vec2 от SUPERB
    model_name = "superb/wav2vec2-base-superb-er"
    
    try:
        feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(model_name)
        model = Wav2Vec2ForSequenceClassification.from_pretrained(model_name, use_safetensors=False)
    except Exception as e:
        print(f"Ошибка загрузки модели: {e}")
        return

    print(f"Загрузка аудиофайла {os.path.basename(file_path)}...")
    speech, sr = librosa.load(file_path, sr=16000)
    
    # Задаем размер сегмента (например, 10 секунд)
    segment_duration = 10  # в секундах
    samples_per_segment = segment_duration * sr
    total_samples = len(speech)
    
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
    
    print(f"\n=== ПОСЕГМЕНТНЫЙ ТАЙМЛАЙН ЭМОЦИЙ ({segment_duration}-секундные интервалы) ===")
    
    num_segments = int(np.ceil(total_samples / samples_per_segment))
    
    output_dir = ".tech"
    os.makedirs(output_dir, exist_ok=True)
    audio_basename = os.path.basename(file_path)
    output_path = os.path.join(output_dir, f"{audio_basename}_local_segments.md")

    md_content = f"# Посегментный таймлайн эмоций\n\n"
    md_content += f"* **Аудиофайл**: `{audio_basename}`\n"
    md_content += f"* **Интервал сегментов**: {segment_duration} сек.\n\n"
    md_content += "| Временной интервал | Преобладающая эмоция | Уверенность |\n"
    md_content += "| :--- | :--- | :--- |\n"
    
    for i in range(num_segments):
        start_sample = i * samples_per_segment
        end_sample = min((i + 1) * samples_per_segment, total_samples)
        
        # Если сегмент слишком короткий (меньше 1 секунды), пропускаем
        if end_sample - start_sample < sr:
            continue
            
        segment = speech[start_sample:end_sample]
        
        # Получаем эмоции для данного сегмента
        inputs = feature_extractor(segment, sampling_rate=16000, return_tensors="pt", padding=True)
        
        with torch.no_grad():
            logits = model(**inputs).logits
            
        scores = torch.softmax(logits, dim=-1).numpy()[0]
        
        # Находим преобладающую эмоцию
        max_idx = np.argmax(scores)
        dominant_emotion = emotions_labels[max_idx]
        confidence = scores[max_idx]
        
        start_sec = start_sample / sr
        end_sec = end_sample / sr
        
        print(f"Интервал {start_sec:05.1f}s - {end_sec:05.1f}s | Преобладающая эмоция: {dominant_emotion:<12} (уверенность: {confidence:.1%})")
        md_content += f"| {start_sec:05.1f}s - {end_sec:05.1f}s | **{dominant_emotion}** | {confidence:.1%} |\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"\n[Успешно] Результат сохранен в: {output_path}")

if __name__ == "__main__":
    analyze_audio_segments(audio_path)
