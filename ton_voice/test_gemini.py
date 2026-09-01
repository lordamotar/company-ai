import os
import time
from dotenv import load_dotenv
from google import genai

# Загружаем переменные окружения из файла .env
load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("Пожалуйста, установите переменную окружения GEMINI_API_KEY перед запуском.")

# Интерактивный выбор версии модели Gemini
print("=== Выберите версию модели Gemini ===")
print("1: gemini-3.6-flash (Рекомендуемая, новейшая)")
print("2: gemini-2.5-flash")
choice = input("Введите номер модели (1 или 2, по умолчанию 1): ").strip()

if choice == "2":
    model_name = "gemini-2.5-flash"
    version_label = "2.5"
else:
    model_name = "gemini-3.6-flash"
    version_label = "3.6"

print(f"\nВыбрана модель: {model_name} (версия {version_label})")

# Инициализируем клиента с использованием нового SDK google-genai
client = genai.Client(api_key=api_key)

# Путь к аудиофайлу
audio_file_path = r"incoming/rg-5555-87014911924-20201106-145444-1604652884.43624.wav"

if not os.path.exists(audio_file_path):
    raise FileNotFoundError(f"Аудиофайл не найден по пути: {audio_file_path}")

print("Загрузка аудиофайла в Gemini API...")
# Загружаем файл на серверы Google
audio_file = client.files.upload(file=audio_file_path)
print(f"Файл загружен. Имя на сервере: {audio_file.name}")

print(f"Транскрибируем и анализируем звонок через Gemini {version_label} Transcribe...")
max_retries = 3
retry_delay = 5
response = None

for attempt in range(max_retries):
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[
                audio_file,
                "Сделай дословную транскрипцию этого звонка на русском языке. "
                "Раздели диалог на Менеджера и Клиента."
            ]
        )
        break
    except Exception as e:
        if "503" in str(e) or "UNAVAILABLE" in str(e) or "429" in str(e):
            print(f"Сервер временно перегружен (попытка {attempt + 1} из {max_retries}). Повтор через {retry_delay} сек...")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
        raise e

if response:
    print(f"\n--- РЕЗУЛЬТАТ ОТ GEMINI {version_label} ---")
    print(response.text)
    
    # Сохраняем результат в папку .tech
    audio_basename = os.path.basename(audio_file_path)
    output_dir = ".tech"
    os.makedirs(output_dir, exist_ok=True)
    output_filename = f"{audio_basename}_API_gemini_{version_label}_result.md"
    output_path = os.path.join(output_dir, output_filename)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(response.text)
    print(f"\n[Успешно] Результат сохранен в файл: {output_path}")

# Очищаем файл на серверах Google
client.files.delete(name=audio_file.name)
print("Файл на сервере удален. Тест завершен.")
