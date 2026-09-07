"""
AIKOS CRM Voice Analytics Web Server
Запуск FastAPI приложения через uvicorn.
"""
import sys
import uvicorn
from app.core.config import settings
from app.main import app

if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

    print("=" * 60)
    print(f"🚀 AIKOS CRM FastAPI Сервер запущен на: http://{settings.HOST}:{settings.PORT}")
    print(f"📚 Swagger UI Документация доступна на: http://localhost:{settings.PORT}/docs")
    print(f"📁 Обслуживаемая папка статики: {settings.STATIC_DIR}")
    print("=" * 60)
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT)
