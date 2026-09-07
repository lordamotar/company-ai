from typing import List
from fastapi import APIRouter
from app.models.schemas import CallUpdateRequest, CallUpdateResponse
from app.services.call_service import update_call_details, get_all_calls, get_call_by_id, get_all_audit_logs

router = APIRouter(tags=["Calls Analytics"])

@router.get("/calls", summary="Получение списка всех звонков с аналитикой")
async def list_calls():
    """Возвращает полный список звонков с эмоциями, чек-листами, транскрибациями и метриками."""
    return get_all_calls()

@router.get("/calls/{call_id}", summary="Получение детальной информации по одному звонку")
async def get_call(call_id: int):
    """Возвращает подробные данные по конкретному звонку по его ID."""
    return get_call_by_id(call_id)

@router.post("/calls/update", response_model=CallUpdateResponse, summary="Обновление данных звонка и запись в аудит")
async def update_call(data: CallUpdateRequest):
    """
    Обновляет данные звонка (имя менеджера, город/филиал) в PostgreSQL
    и фиксирует историю изменения в таблице call_audit_logs.
    """
    return update_call_details(data)

@router.get("/audit", summary="Получение журнала аудита ручных корректировок")
async def list_audit_logs():
    """Возвращает историю всех изменений параметров звонков."""
    return get_all_audit_logs()
