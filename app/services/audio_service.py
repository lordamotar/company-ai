import os
import logging
from pathlib import Path
from typing import Optional, Tuple
from app.core.config import settings
from app.core.database import get_db_connection

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = ('.wav', '.mp3', '.ogg', '.m4a')

def find_audio_file(
    call_id: Optional[int] = None,
    source_file: Optional[str] = None,
    unique_id: Optional[str] = None,
    phone: Optional[str] = None
) -> Optional[str]:
    """Ищет аудиофайл по ID звонка, имени файла, unique_id или номеру телефона."""
    # 1. Если передан call_id, получаем дополнительную информацию из PostgreSQL
    if call_id:
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT source_file, unique_id, phone FROM call_transcriptions WHERE id = %s;", (call_id,))
                    row = cur.fetchone()
                    if row:
                        if not source_file and row[0]: source_file = row[0]
                        if not unique_id and row[1]: unique_id = row[1]
                        if not phone and row[2]: phone = row[2]
        except Exception as e:
            logger.warning(f"[!] Ошибка БД при поиске аудио: {e}")

    # 2. Ищем прямо по имени исходного файла
    if source_file:
        base_name = os.path.basename(source_file)
        for adir in settings.AUDIO_DIRS:
            candidate = os.path.join(adir, base_name)
            if os.path.isfile(candidate):
                return candidate

    # 3. Ищем по unique_id
    if unique_id:
        for adir in settings.AUDIO_DIRS:
            if not os.path.isdir(adir): continue
            for fname in os.listdir(adir):
                if unique_id in fname and fname.lower().endswith(AUDIO_EXTENSIONS):
                    return os.path.join(adir, fname)

    # 4. Ищем по номеру телефона
    if phone:
        clean_phone = "".join(filter(str.isdigit, phone))
        if len(clean_phone) >= 7:
            for adir in settings.AUDIO_DIRS:
                if not os.path.isdir(adir): continue
                for fname in os.listdir(adir):
                    if clean_phone in fname and fname.lower().endswith(AUDIO_EXTENSIONS):
                        return os.path.join(adir, fname)

    return None

def get_audio_media_type(file_path: str) -> str:
    """Определяет MIME-тип аудиофайла по его расширению."""
    ext = os.path.splitext(file_path)[1].lower()
    mapping = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4"
    }
    return mapping.get(ext, "application/octet-stream")
