import sys
import os
import re
import time
import yt_dlp
import questionary
import torch
from faster_whisper import WhisperModel
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, SpinnerColumn
from rich.prompt import Prompt

console = Console()

# Словари локализации
STRINGS = {
    "ru": {
        "title": "🚀 Универсальный Транскрибатор [GPU EDITION]",
        "gpu_found": "[bold green]GPU обнаружен:[/bold green] {}",
        "no_gpu": "[bold red]ВНИМАНИЕ:[/bold red] NVIDIA GPU не обнаружен. Продолжить?",
        "select_model": "Выберите модель (GPU потянет Large):",
        "ask_timestamps": "Добавить таймкоды (SRT)?",
        "with_timestamps": "Да (сохранить TXT и SRT)",
        "no_timestamps": "Нет (только чистый TXT)",
        "input_prompt": "\n[bold yellow]🔗 Вставьте ссылку ИЛИ путь к .txt файлу со списком[/bold yellow]",
        "file_detected": "[bold blue]📂 Чтение списка из файла...[/bold blue]",
        "links_found": "[bold blue]Найдено ссылок: {}[/bold blue]",
        "model_loading": "[bold magenta]⚙️ Загрузка модели '{}' на GPU...[/bold magenta]",
        "processing_video": "\n[bold yellow]>>> Видео {} из {}[/bold yellow]",
        "downloading": "[bold blue]📥 Скачивание...[/bold blue]",
        "transcribing": "⚡ GPU Обработка",
        "saving": "[bold green]💾 Сохранение...[/bold green]",
        "done": "[bold green]✅ Готово![/bold green]",
        "success": "[bold green]✨ Результат:[/bold green] {}",
        "error_download": "[bold red]![/bold red] Ошибка скачивания {}: {}",
        "error_transcribe": "[bold red]![/bold red] Ошибка GPU транскрибации: {}",
        "error_input": "[bold red]Ошибка:[/bold red] Введите корректную ссылку или путь к файлу.",
        "list_empty": "[bold red]Список пуст.[/bold red]",
        "stopped": "\n[bold red]✖ Остановлено.[/bold red]",
    },
    "en": {
        "title": "🚀 Universal Transcriber [GPU EDITION]",
        "gpu_found": "[bold green]GPU Found:[/bold green] {}",
        "no_gpu": "[bold red]WARNING:[/bold red] NVIDIA GPU not found. Continue?",
        "select_model": "Select model (GPU can handle Large):",
        "ask_timestamps": "Include timestamps (SRT)?",
        "with_timestamps": "Yes (save TXT and SRT)",
        "no_timestamps": "No (save only clean TXT)",
        "input_prompt": "\n[bold yellow]🔗 Paste URL OR path to .txt file with links[/bold yellow]",
        "file_detected": "[bold blue]📂 Reading list from file...[/bold blue]",
        "links_found": "[bold blue]Links found: {}[/bold blue]",
        "model_loading": "[bold magenta]⚙️ Loading model '{}' to GPU...[/bold magenta]",
        "processing_video": "\n[bold yellow]>>> Video {} of {}[/bold yellow]",
        "downloading": "[bold blue]📥 Downloading...[/bold blue]",
        "transcribing": "⚡ GPU Processing",
        "saving": "[bold green]💾 Saving...[/bold green]",
        "done": "[bold green]✅ Done![/bold green]",
        "success": "[bold green]✨ Result:[/bold green] {}",
        "error_download": "[bold red]![/bold red] Download error {}: {}",
        "error_transcribe": "[bold red]![/bold red] GPU Transcription error: {}",
        "error_input": "[bold red]Error:[/bold red] Enter valid URL or file path.",
        "list_empty": "[bold red]Link list is empty.[/bold red]",
        "stopped": "\n[bold red]✖ Stopped.[/bold red]",
    }
}

def clean_filename(filename):
    if not filename: return f"transcription_{int(time.time())}"
    return re.sub(r'[\\/*?:"<>|]', "", filename)

def check_cuda(lang_dict):
    if not torch.cuda.is_available():
        console.print(lang_dict["no_gpu"])
        return questionary.confirm("Продолжить? / Continue?").ask()
    console.print(lang_dict["gpu_found"].format(torch.cuda.get_device_name(0)))
    return True

def format_timestamp(seconds: float):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def download_audio(url, progress, task_id, lang_dict):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'm4a'}],
        'outtmpl': '%(title)s.%(ext)s', 'quiet': True, 'no_warnings': True, 'noplaylist': True,
    }
    try:
        progress.update(task_id, description=lang_dict["downloading"])
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_title = info.get('title') or info.get('id') or f"video_{int(time.time())}"
            temp_filename = ydl.prepare_filename(info)
            base, _ = os.path.splitext(temp_filename)
            actual_filename = base + ".m4a"
            progress.update(task_id, advance=15)
            if os.path.exists(actual_filename): return actual_filename, video_title
            return None, None
    except Exception as e:
        console.print(lang_dict["error_download"].format(url, e))
        return None, None

def get_bar_color(percentage):
    if percentage < 25: return "red"
    elif percentage < 50: return "orange3"
    elif percentage < 75: return "yellow"
    else: return "green"

def transcribe_audio(audio_path, model, progress, task_id, lang_dict):
    try:
        progress.update(task_id, description=f"[bold cyan]{lang_dict['transcribing']}...[/bold cyan]")
        segments, info = model.transcribe(audio_path, beam_size=5)
        total_duration = info.duration
        
        full_text = []
        srt_lines = []
        start_points = progress.tasks[task_id].completed
        
        for i, segment in enumerate(segments, start=1):
            full_text.append(segment.text)
            
            # Формируем SRT
            start_ts = format_timestamp(segment.start)
            end_ts = format_timestamp(segment.end)
            srt_lines.append(f"{i}\n{start_ts} --> {end_ts}\n{segment.text.strip()}\n")
            
            if total_duration > 0:
                sub_progress = (segment.end / total_duration) * 75
                current_total = min(start_points + sub_progress, 99)
                color = get_bar_color(current_total)
                progress.update(task_id, completed=current_total, 
                                description=f"[bold {color}]{lang_dict['transcribing']} ({int(current_total)}%)...[/bold {color}]")
                                
        return " ".join(full_text), "\n".join(srt_lines)
    except Exception as e:
        console.print(lang_dict["error_transcribe"].format(e))
        return None, None

def process_url(url, model, lang_dict, include_srt):
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), BarColumn(bar_width=40),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"), TimeElapsedColumn(),
        console=console
    ) as progress:
        task_id = progress.add_task(f"...", total=100)
        audio_path, video_title = download_audio(url, progress, task_id, lang_dict)
        if not audio_path: return
        
        text, srt_text = transcribe_audio(audio_path, model, progress, task_id, lang_dict)
        
        if text:
            progress.update(task_id, description=lang_dict["saving"])
            
            result_dir = "result"
            os.makedirs(result_dir, exist_ok=True)
            
            safe_title = clean_filename(video_title)
            txt_filename = os.path.join(result_dir, f"{safe_title}.txt")
            
            with open(txt_filename, "w", encoding="utf-8") as f: f.write(text)
            saved_files = txt_filename
            
            if include_srt and srt_text:
                srt_filename = os.path.join(result_dir, f"{safe_title}.srt")
                with open(srt_filename, "w", encoding="utf-8") as f: f.write(srt_text)
                saved_files += f" + {srt_filename}"
            
            progress.update(task_id, completed=100, description=lang_dict["done"])
            console.print(lang_dict["success"].format(saved_files))
            
        if audio_path and os.path.exists(audio_path):
            try: os.remove(audio_path)
            except: pass

def main():
    # 0. Выбор языка
    lang_choice = questionary.select(
        "Выберите язык / Select Language:",
        choices=[
            questionary.Choice("Русский", value="ru"),
            questionary.Choice("English", value="en"),
        ]
    ).ask()
    
    if not lang_choice: return
    lang = STRINGS[lang_choice]

    console.print(f"\n[bold cyan]{lang['title']}[/bold cyan]\n")

    if not check_cuda(lang): return

    # 1. Выбор модели
    model_size = questionary.select(
        lang["select_model"],
        choices=[
            questionary.Choice("Large-v3", value="large-v3"),
            questionary.Choice("Medium", value="medium"),
            questionary.Choice("Small", value="small"),
            questionary.Choice("Base", value="base"),
        ]
    ).ask()

    if not model_size: return

    # 1.5. Выбор таймкодов (SRT)
    include_srt = questionary.select(
        lang["ask_timestamps"],
        choices=[
            questionary.Choice(lang["with_timestamps"], value=True),
            questionary.Choice(lang["no_timestamps"], value=False),
        ]
    ).ask()

    if include_srt is None: return

    # 2. Ввод
    user_input = Prompt.ask(lang["input_prompt"]).strip()

    urls = []
    if os.path.isfile(user_input):
        console.print(lang["file_detected"])
        with open(user_input, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip().startswith("http")]
        console.print(lang["links_found"].format(len(urls)))
    else:
        if user_input.startswith("http"): urls = [user_input]
        else:
            console.print(lang["error_input"])
            return

    if not urls:
        console.print(lang["list_empty"])
        return

    console.print(lang["model_loading"].format(model_size))
    model = WhisperModel(model_size, device="cuda", compute_type="float16")

    for i, url in enumerate(urls, 1):
        if len(urls) > 1:
            console.print(lang["processing_video"].format(i, len(urls)))
        process_url(url, model, lang, include_srt)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt:
        console.print("\n[bold red]✖ Stopped / Остановлено.[/bold red]")
