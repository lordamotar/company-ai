from fastapi import APIRouter
from app.api.routers import calls, audio, system

api_router = APIRouter(prefix="/api")

api_router.include_router(calls.router)
api_router.include_router(audio.router)
api_router.include_router(system.router)
