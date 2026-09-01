import os
import numpy as np

# Входной тестовый аудиофайл из папки incoming
audio_path = r"incoming/rg-5555-87014662990-20201113-150553-1605258353.53425.wav"

def analyze_audio_features(file_path):
    if not os.path.exists(file_path):
        print(f"Файл {file_path} не найден. Пожалуйста, положите WAV-файл в папку incoming.")
        return

    try:
        import librosa
    except ImportError:
        print("\nБиблиотека librosa не установлена. Установите её командой:")
        print("uv add librosa soundfile")
        return

    print(f"Загрузка аудиофайла {os.path.basename(file_path)}...")
    # Загружаем аудио
    y, sr = librosa.load(file_path, sr=None)
    duration = librosa.get_duration(y=y, sr=sr)
    
    print("\n=== АКУСТИЧЕСКИЙ АНАЛИЗ РАЗГОВОРА (Громкость и Темп) ===")
    print(f"Длительность записи: {duration:.2f} сек.")
    
    # 1. Анализ громкости (Root Mean Square Energy)
    rms = librosa.feature.rms(y=y)[0]
    mean_rms = np.mean(rms)
    max_rms = np.max(rms)
    
    print(f"Средняя громкость разговора (RMS): {mean_rms:.4f}")
    print(f"Пиковая громкость разговора (RMS): {max_rms:.4f}")
    
    # Поиск потенциальных криков (резкие всплески громкости)
    loud_threshold = mean_rms * 2.5
    loud_frames = np.sum(rms > loud_threshold)
    loud_percentage = (loud_frames / len(rms)) * 100
    print(f"Доля громких моментов (потенциальный крик/всплеск): {loud_percentage:.2f}%")

    # 2. Анализ пауз и тишины (Silence Detection)
    # Определяем тишину на основе порога в децибелах (по умолчанию -40 дБ относительно пика)
    non_silent_intervals = librosa.effects.split(y, top_db=40)
    non_silent_duration = sum((end - start) for start, end in non_silent_intervals) / sr
    silent_duration = duration - non_silent_duration
    silence_percentage = (silent_duration / duration) * 100
    
    print(f"Суммарное время тишины (пауз): {silent_duration:.2f} сек. ({silence_percentage:.2f}% от звонка)")
    print(f"Время активного разговора: {non_silent_duration:.2f} сек.")

    # 3. Анализ темпа речи (ориентировочный Tempo)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    tempo_val = float(tempo[0]) if isinstance(tempo, (list, np.ndarray)) else float(tempo)
    # Метод возвращает число ударов/ритмических долей в минуту (для речи это коррелирует со скоростью речи)
    print(f"Оценочный темп речи (ритмический показатель): {tempo_val:.1f} ударов/мин")

    # Сохраняем в md
    output_dir = ".tech"
    os.makedirs(output_dir, exist_ok=True)
    audio_basename = os.path.basename(file_path)
    output_path = os.path.join(output_dir, f"{audio_basename}_local_features.md")

    md_content = f"# Акустический анализ разговора (Громкость и Темп)\n\n"
    md_content += f"* **Аудиофайл**: `{audio_basename}`\n"
    md_content += f"* **Длительность записи**: {duration:.2f} сек.\n\n"
    md_content += "## 1. Анализ громкости\n"
    md_content += f"* Средняя громкость (RMS): **{mean_rms:.4f}**\n"
    md_content += f"* Пиковая громкость (RMS): **{max_rms:.4f}**\n"
    md_content += f"* Доля громких моментов (потенциальный крик): **{loud_percentage:.2f}%**\n\n"
    md_content += "## 2. Анализ тишины и пауз\n"
    md_content += f"* Время активного разговора: **{non_silent_duration:.2f} сек.**\n"
    md_content += f"* Суммарное время тишины (пауз): **{silent_duration:.2f} сек. ({silence_percentage:.2f}%)**\n\n"
    md_content += "## 3. Темп речи\n"
    md_content += f"* Оценочный темп речи (ритмический показатель): **{tempo_val:.1f} ударов/мин**\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"\n[Успешно] Результат сохранен в: {output_path}")

if __name__ == "__main__":
    analyze_audio_features(audio_path)
