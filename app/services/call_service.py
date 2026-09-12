from typing import List, Optional
from fastapi import HTTPException
from app.core.database import get_db_connection
from app.models.schemas import CallUpdateRequest, CallUpdateResponse
from app.services.export_service import sync_data_if_needed
from export_db_to_json import export_calls_to_json, export_audit_logs_to_json

def get_all_calls() -> List[dict]:
    """Возвращает список всех звонков с расширенной аналитикой."""
    try:
        calls = export_calls_to_json(write_to_file=False)
        return calls
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки звонков из БД: {e}")

def get_call_by_id(call_id: int) -> dict:
    """Возвращает данные конкретного звонка по ID."""
    calls = get_all_calls()
    for call in calls:
        if call.get("id") == call_id:
            return call
    raise HTTPException(status_code=404, detail=f"Звонок с ID {call_id} не найден")

def get_all_audit_logs() -> List[dict]:
    """Возвращает журнал аудита всех ручных изменений."""
    try:
        logs = export_audit_logs_to_json(write_to_file=False)
        return logs
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки журнала аудита: {e}")

def update_call_details(data: CallUpdateRequest) -> CallUpdateResponse:
    """Обновляет имя менеджера и город в звонке, фиксируя историю в call_audit_logs."""
    call_id = data.call_id
    new_manager = data.manager_name.strip() or "Оператор"
    new_city = data.city_name.strip() or "Алматы"
    changed_by = data.changed_by.strip() or "Супервайзер"
    comment = data.comment.strip() or "Ручная корректировка данных"

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # 1. Получаем текущие сохраненные данные
                cur.execute("SELECT manager_name, city_name FROM call_transcriptions WHERE id = %s;", (call_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail=f"Звонок с ID {call_id} не найден в базе данных")

                old_manager = row[0] if row[0] else "Оператор"
                old_city = row[1] if row[1] else "Алматы"

                # 2. Обновляем в call_transcriptions
                cur.execute("""
                    UPDATE call_transcriptions 
                    SET manager_name = %s, city_name = %s 
                    WHERE id = %s;
                """, (new_manager, new_city, call_id))

                # 3. Логируем изменения менеджера
                if old_manager != new_manager:
                    cur.execute("""
                        INSERT INTO call_audit_logs (call_id, field_name, old_value, new_value, changed_by, comment)
                        VALUES (%s, 'manager_name', %s, %s, %s, %s);
                    """, (call_id, old_manager, new_manager, changed_by, comment))

                # 4. Логируем изменения города
                if old_city != new_city:
                    cur.execute("""
                        INSERT INTO call_audit_logs (call_id, field_name, old_value, new_value, changed_by, comment)
                        VALUES (%s, 'city_name', %s, %s, %s, %s);
                    """, (call_id, old_city, new_city, changed_by, comment))

                conn.commit()

        # 5. Мгновенно синхронизируем данные
        sync_data_if_needed(force=True)

        return CallUpdateResponse(
            success=True,
            message=f"Данные по звонку #{call_id} успешно обновлены и зафиксированы в журнале аудита.",
            call_id=call_id,
            manager_name=new_manager,
            city_name=new_city
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
