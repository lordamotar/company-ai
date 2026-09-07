from fastapi import APIRouter
from app.core.config import settings
from app.core.database import get_db_connection
from app.models.schemas import HealthCheckResponse

router = APIRouter(prefix="/system", tags=["System Health"])

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
