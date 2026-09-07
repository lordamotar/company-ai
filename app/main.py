import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from app.core.config import settings
from app.api.api import api_router
from app.services.call_service import get_all_calls, get_all_audit_logs
from app.services.export_service import sync_data_if_needed

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Инициализация AIKOS CRM Voice Analytics FastAPI сервиса...")
    sync_data_if_needed(force=True)
    yield
    print("🛑 Остановка сервиса.")

def create_application() -> FastAPI:
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description=settings.DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json"
    )

    # CORS Middleware
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Подключение всех эндпоинтов API (/api/calls, /api/audio, /api/audit, /api/system)
    application.include_router(api_router)

    # Обратная совместимость для прямых путей /calls_data.json и /audit_data.json
    @application.get("/calls_data.json", tags=["Data Export"], summary="JSON данные звонков (обратная совместимость)")
    async def get_calls_data_compat():
        return get_all_calls()

    @application.get("/audit_data.json", tags=["Data Export"], summary="JSON журнала аудита (обратная совместимость)")
    async def get_audit_data_compat():
        return get_all_audit_logs()

    # Раздача фронтенда (HTML, CSS, JS)
    if settings.STATIC_DIR.is_dir():
        application.mount("/", StaticFiles(directory=str(settings.STATIC_DIR), html=True), name="static")

    return application

app = create_application()

def run():
    print("=" * 60)
    print(f"🚀 AIKOS CRM FastAPI Сервер запущен на: http://{settings.HOST}:{settings.PORT}")
    print(f"📚 Swagger UI Документация доступна на: http://localhost:{settings.PORT}/docs")
    print(f"📁 Обслуживаемая папка статики: {settings.STATIC_DIR}")
    print("=" * 60)
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=False)

if __name__ == "__main__":
    run()
