"""API справочника филиалов (страница «Филиалы»): список с числом звонков, добавление, правка, удаление,
синонимы и названия из звонков, которые пока не сопоставились ни с одним филиалом."""
from typing import List
import psycopg2
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_db_connection
import branch_registry as br

router = APIRouter(prefix="/branches", tags=["Branches"])


class BranchIn(BaseModel):
    city: str = Field(..., description="Город филиала", examples=["Семей"])
    name: str = Field(..., description="Название филиала / пункта", examples=["Красный пахарь"])
    address: str = Field(default="", description="Адрес", examples=["трасса Семей — Павлодар"])
    aliases: List[str] = Field(default_factory=list, description="Другие написания из звонков", examples=[["Красный Пильщик"]])


class AliasIn(BaseModel):
    alias: str = Field(..., min_length=1, max_length=128)


def _run(action):
    """Выполняет действие в транзакции с единообразной обработкой ошибок."""
    try:
        with get_db_connection() as conn:
            br.ensure_schema(conn)
            cur = conn.cursor()
            try:
                result = action(cur)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except psycopg2.IntegrityError:
        raise HTTPException(status_code=409, detail="Такой филиал уже есть в этом городе.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка работы со справочником филиалов: {e}")


@router.get("", summary="Справочник филиалов: филиалы с числом звонков и несопоставленные названия из звонков")
async def overview():
    return _run(br.branch_usage)


@router.post("", summary="Добавить филиал")
async def create_branch(data: BranchIn):
    return _run(lambda cur: br.add_branch(cur, data.city, data.name, data.address, data.aliases))


@router.put("/{branch_id}", summary="Изменить филиал")
async def edit_branch(branch_id: int, data: BranchIn):
    def act(cur):
        row = br.update_branch(cur, branch_id, data.city, data.name, data.address, data.aliases)
        if not row:
            raise HTTPException(status_code=404, detail="Филиал не найден.")
        return row
    return _run(act)


@router.delete("/{branch_id}", summary="Удалить филиал (звонки не меняются, у них остаётся исходное название)")
async def remove_branch(branch_id: int):
    def act(cur):
        if not br.delete_branch(cur, branch_id):
            raise HTTPException(status_code=404, detail="Филиал не найден.")
        return {"success": True}
    return _run(act)


@router.post("/{branch_id}/aliases", summary="Добавить написание из звонка как синоним филиала")
async def create_alias(branch_id: int, data: AliasIn):
    def act(cur):
        row = br.add_alias(cur, branch_id, data.alias)
        if not row:
            raise HTTPException(status_code=404, detail="Филиал не найден.")
        return row
    return _run(act)
