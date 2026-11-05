from fastapi import APIRouter
from pydantic import BaseModel
from app.core.config import settings
from app.core.database import get_db_connection
from app.models.schemas import HealthCheckResponse
from app.services.export_service import sync_data_if_needed
from export_db_to_json import get_tone_enabled, set_tone_enabled

router = APIRouter(prefix="/system", tags=["System Health"])


class SystemSettings(BaseModel):
    toneEnabled: bool


@router.get("/settings", response_model=SystemSettings, summary="Настройки системы")
async def read_settings():
    return SystemSettings(toneEnabled=get_tone_enabled())


@router.put("/settings", response_model=SystemSettings, summary="Изменение настроек системы")
async def update_settings(data: SystemSettings):
    set_tone_enabled(data.toneEnabled)
    sync_data_if_needed(force=True)   # calls_data.json — запасной источник данных интерфейса
    return data


@router.get("/health", response_model=HealthCheckResponse, summary="Проверка состояния сервера и базы данных")
async def health_check():
    db_status = "ok"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
    except Exception as e:
        db_status = f"error: {e}"

    return HealthCheckResponse(
        status="healthy" if db_status == "ok" else "degraded",
        version=settings.VERSION,
        database=db_status
    )
