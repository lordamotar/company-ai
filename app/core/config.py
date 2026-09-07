import os
from pathlib import Path
from typing import List

# Базовая директория проекта
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings:
    PROJECT_NAME: str = "AIKOS CRM Voice Analytics API"
    VERSION: str = "2.0.0"
    DESCRIPTION: str = "FastAPI бэкенд речевой аналитики и контроля стандартов обслуживания AIKOS CRM"

    # Сетевые настройки
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8080"))

    # Директория статики (фронтенд)
    STATIC_DIR: Path = Path(os.getenv("STATIC_DIR", BASE_DIR / "frontend"))

    # Директории поиска аудиозаписей
    AUDIO_DIRS: List[Path] = [
        BASE_DIR / "incoming",
        BASE_DIR / ".tech" / "call",
        BASE_DIR / "processed",
        BASE_DIR / "processing",
    ]

    # Настройки PostgreSQL
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT: int = int(os.getenv("DB_PORT", "5433"))
    DB_NAME: str = os.getenv("DB_NAME", "company")
    DB_USER: str = os.getenv("DB_USER", "company_user")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "company_password")

    @property
    def DB_CONFIG(self) -> dict:
        return {
            "host": self.DB_HOST,
            "port": self.DB_PORT,
            "database": self.DB_NAME,
            "user": self.DB_USER,
            "password": self.DB_PASSWORD,
        }

settings = Settings()
