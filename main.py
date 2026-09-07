"""
AIKOS CRM Voice Analytics — Root Entry Point
Экспортирует экземпляр FastAPI приложения `app` для uvicorn / gunicorn.
"""
from app.main import app, run

if __name__ == "__main__":
    run()
