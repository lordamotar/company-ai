import os
import sys
import glob

# Путь к папке с транскрипциями
transcriptions_dir = "transcriptions"
# Папка для сохранения результатов анализа
output_dir = os.path.join(".tech", "md")

def analyze_real_transcriptions():
    if not os.path.exists(transcriptions_dir):
        print(f"Папка {transcriptions_dir} не найдена.")
        return

    try:
        print("Импорт библиотек PyTorch и Transformers (может занять время)...")
        from transformers import pipeline
    except ImportError:
        print("\nБиблиотеки не установлены. Установите их командой:")
        print("uv add transformers torch")
        return

    # Находим все .txt файлы в папке transcriptions
    txt_files = glob.glob(os.path.join(transcriptions_dir, "*.txt"))
    if not txt_files:
        print(f"В папке {transcriptions_dir} не найдено .txt файлов для анализа.")
        return

    print("Загрузка модели RuBERT-sentiment с Hugging Face...")
    model_name = "blanchefort/rubert-base-cased-sentiment"
    
    try:
        classifier = pipeline("sentiment-analysis", model=model_name)
    except Exception as e:
        print(f"Ошибка загрузки модели: {e}")
        return

    # Создаем выходную папку .tech/md
    os.makedirs(output_dir, exist_ok=True)

    print(f"\nНайдено файлов для анализа: {len(txt_files)}")
    
    for file_path in txt_files:
        filename = os.path.basename(file_path)
        print(f"\nОбработка файла: {filename}...")
        
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
            
        # Разбиваем текст на строки (реплики) и отфильтровываем пустые
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        
        if not lines:
            print(f"Файл {filename} пуст. Пропускаем.")
            continue
            
        # Начинаем формировать Markdown-отчет
        # Преобразуем имя в формат .wav_local_rubert.md
        base_name = os.path.splitext(filename)[0]
        output_filename = f"{base_name}.wav_local_rubert.md"
        output_path = os.path.join(output_dir, output_filename)
        
        md_content = f"# Анализ тональности реплик (RuBERT Sentiment)\n\n"
        md_content += f"* **Исходный файл транскрипции**: `{filename}`\n"
        md_content += f"* **Всего проанализировано реплик**: {len(lines)}\n\n"
        md_content += "| Текст реплики | Тональность | Уверенность |\n"
        md_content += "| :--- | :--- | :--- |\n"
        
        for line in lines:
            try:
                # Ограничиваем длину строки на всякий случай для BERT (макс 512 токенов)
                truncated_line = line[:500]
                result = classifier(truncated_line)[0]
                label = result['label']
                score = result['score']
                
                # Переводим метки на русский язык
                if label == "NEGATIVE":
                    ru_label = "🔴 НЕГАТИВНЫЙ"
                elif label == "POSITIVE":
                    ru_label = "🟢 ПОЗИТИВНЫЙ"
                else:
                    ru_label = "🟡 НЕЙТРАЛЬНЫЙ"
                
                md_content += f"| {line} | {ru_label} | {score:.2%} |\n"
            except Exception as e:
                md_content += f"| {line} | ⚠️ Ошибка анализа ({str(e)}) | - |\n"
                
        # Записываем отчет
        with open(output_path, "w", encoding="utf-8") as out_f:
            out_f.write(md_content)
            
        print(f"[Успешно] Отчет сохранен в: {output_path}")

if __name__ == "__main__":
    analyze_real_transcriptions()
