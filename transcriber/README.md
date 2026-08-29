# Universal Media Transcriber 🎥 -> 📝

[RU] Автоматическая транскрибация видео и аудио в текст. Две версии: для процессора (CPU) и видеокарты (GPU).  
[EN] Automatic transcription of video and audio to text. Two versions: for CPU and GPU.

---

## 🇷🇺 Русский (RU)

### Выбор версии
В проекте доступны два скрипта:
1.  **`transcribe_cpu.py`**: Оптимизирован для процессоров. Работает без системных зависимостей (не нужен `ffmpeg`). 
2.  **`transcribe_gpu.py`**: Оптимизирован для NVIDIA GPU (CUDA). Требует установленный `ffmpeg` и видеокарту. В 5-10 раз быстрее.

### Установка
1. Установите базовые зависимости:
```bash
uv sync
```
2. **Только для GPU-версии:** Установите `torch` для работы с видеокартой:
```bash
uv add torch
# Или через pip (рекомендуется для стабильности CUDA):
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

> 💡 **Примечание:** Если в консоли появляется предупреждение от `huggingface_hub` про symlinks, вы можете убрать его, включив **"Режим разработчика"** в Windows (Параметры -> Обновление и безопасность -> Для разработчиков).

### Запуск
- Для процессора: `uv run python transcribe_cpu.py`
- Для видеокарты: `uv run python transcribe_gpu.py`

---

## 🇺🇸 English (EN)

### Choose Your Version
Two scripts are available:
1.  **`transcribe_cpu.py`**: Optimized for CPUs. Self-contained (no `ffmpeg` required).
2.  **`transcribe_gpu.py`**: Optimized for NVIDIA GPUs (CUDA). Requires `ffmpeg` and a GPU. 5-10x faster.

### Installation
1. Install base dependencies:
```bash
uv sync
```
2. **Only for GPU version:** Install `torch` for CUDA support:
```bash
uv add torch
# Or via pip (recommended for CUDA stability):
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

> 💡 **Note:** If you see a warning from `huggingface_hub` about symlinks in the console, you can fix it by enabling **"Developer Mode"** in Windows (Settings -> Update & Security -> For developers).

### Usage
- For CPU: `uv run python transcribe_cpu.py`
- For GPU: `uv run python transcribe_gpu.py`

---

## ☕ Поддержка / Support
**Wallet (TON/USDT):**  
`UQC2O8SxqGZ0VRYPDnNEYd2ASHui0dZF_YIQQ1bD1xnJFy8z`

---

## 🛠️ Технологии / Built with
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [rich](https://github.com/Textualize/rich)
- [questionary](https://github.com/tmbo/questionary)
