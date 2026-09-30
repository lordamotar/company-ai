from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.call_service import get_all_calls
from app.services import manager_analysis_service as svc

router = APIRouter(prefix="/managers", tags=["Manager Analysis"])


def _dates(date_from: Optional[str], date_to: Optional[str]):
    d_from, d_to = svc.parse_iso_date(date_from), svc.parse_iso_date(date_to)
    if date_from and not d_from:
        raise HTTPException(status_code=422, detail="date_from должен быть в формате ГГГГ-ММ-ДД")
    if date_to and not d_to:
        raise HTTPException(status_code=422, detail="date_to должен быть в формате ГГГГ-ММ-ДД")
    if d_from and d_to and d_from > d_to:
        raise HTTPException(status_code=422, detail="Начало периода позже его конца")
    return d_from, d_to


class AiAnalysisRequest(BaseModel):
    manager: str = Field(..., description="Имя менеджера")
    date_from: Optional[str] = Field(default=None, description="Начало периода, ГГГГ-ММ-ДД")
    date_to: Optional[str] = Field(default=None, description="Конец периода, ГГГГ-ММ-ДД")
    refresh: bool = Field(default=False, description="Игнорировать кэш и запросить ИИ заново")


@router.get("", summary="Список менеджеров и доступный период данных")
def managers_list():
    calls = get_all_calls()
    return {"managers": svc.list_managers(calls), "period": svc.list_periods(calls)}


@router.get("/analysis", summary="Детальные метрики менеджера за период (без ИИ)")
def manager_analysis(
    manager: str = Query(..., description="Имя менеджера"),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
):
    d_from, d_to = _dates(date_from, date_to)
    return svc.build_analysis(get_all_calls(), manager, d_from, d_to)


@router.post("/ai-analysis", summary="AI-разбор слабых мест менеджера за период")
def manager_ai_analysis(data: AiAnalysisRequest):
    d_from, d_to = _dates(data.date_from, data.date_to)
    try:
        return svc.run_ai_analysis(get_all_calls(), data.manager, d_from, d_to, refresh=data.refresh)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
