import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from app.services.audio_service import find_audio_file, get_audio_media_type

router = APIRouter(prefix="/audio", tags=["Audio Streaming"])

@router.get("", summary="Стриминг аудиозаписи звонка с поддержкой Range-запросов")
async def stream_audio(
    call_id: Optional[int] = Query(None, description="ID звонка в базе данных"),
    file: Optional[str] = Query(None, description="Имя исходного файла"),
    unique_id: Optional[str] = Query(None, description="Уникальный ID звонка"),
    phone: Optional[str] = Query(None, description="Номер телефона клиента")
):
    """
    Находит аудиозапись в рабочей или архивной директории и отдает потоковое аудио.
    Поддерживает Range-запросы для плавной перемотки в веб-плеере.
    """
    audio_path = find_audio_file(call_id=call_id, source_file=file, unique_id=unique_id, phone=phone)
    if not audio_path or not os.path.isfile(audio_path):
        raise HTTPException(status_code=404, detail="Audio file not found")

    media_type = get_audio_media_type(audio_path)

    return FileResponse(
        path=audio_path,
        media_type=media_type,
        headers={"Accept-Ranges": "bytes"}
    )
