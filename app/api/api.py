from fastapi import APIRouter
from app.api.routers import calls, audio, system, managers, branches

api_router = APIRouter(prefix="/api")

api_router.include_router(calls.router)
api_router.include_router(audio.router)
api_router.include_router(system.router)
api_router.include_router(managers.router)
api_router.include_router(branches.router)
