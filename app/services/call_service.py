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
    """Обновляет имя менеджера, город и коммерческие параметры звонка, фиксируя историю в call_audit_logs."""
    call_id = data.call_id
    new_manager = data.manager_name.strip() or "Оператор"
    new_city = data.city_name.strip() or "Алматы"
    changed_by = data.changed_by.strip() or "Супервайзер"
    comment = data.comment.strip() or "Ручная корректировка данных"

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # 0. Обеспечиваем наличие колонки unique_id в call_audit_logs
                cur.execute("ALTER TABLE call_audit_logs ADD COLUMN IF NOT EXISTS unique_id VARCHAR(64);")

                # 1. Получаем текущие сохраненные данные
                cur.execute("""
                    SELECT unique_id, manager_name, city_name, sale_status, order_sum, has_upsell, upsell_sum, upsell_text, service_booking, service_date 
                    FROM call_transcriptions 
                    WHERE id = %s;
                """, (call_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail=f"Звонок с ID {call_id} не найден в базе данных")

                unique_id = row[0]
                old_manager = row[1] or "Оператор"
                old_city = row[2] or "Алматы"
                old_sale = row[3] or "Консультация"
                old_order_sum = row[4] or "0 ₸"
                old_has_upsell = bool(row[5])
                old_upsell_sum = row[6] or "0 ₸"
                old_upsell_text = row[7] or ""
                old_service = row[8] or "Нет"
                old_service_date = row[9] or ""

                sale_status = data.sale_status if data.sale_status is not None else old_sale
                order_sum = data.order_sum if data.order_sum is not None else old_order_sum
                has_upsell = data.has_upsell if data.has_upsell is not None else old_has_upsell
                upsell_sum = data.upsell_sum if data.upsell_sum is not None else old_upsell_sum
                upsell_text = data.upsell_text if data.upsell_text is not None else old_upsell_text
                service_booking = data.service_booking if data.service_booking is not None else old_service
                service_date = data.service_date if data.service_date is not None else old_service_date

                # 2. Обновляем в call_transcriptions
                cur.execute("""
                    UPDATE call_transcriptions 
                    SET manager_name = %s, city_name = %s,
                        sale_status = %s, order_sum = %s, has_upsell = %s, upsell_sum = %s, upsell_text = %s, service_booking = %s, service_date = %s,
                        client_name = COALESCE(%s, client_name),
                        car_model = COALESCE(%s, car_model),
                        tire_brand = COALESCE(%s, tire_brand),
                        tire_size = COALESCE(%s, tire_size),
                        tire_season = COALESCE(%s, tire_season),
                        tire_count = COALESCE(%s, tire_count),
                        branch_address = COALESCE(%s, branch_address),
                        payment_method = COALESCE(%s, payment_method),
                        lost_reason = COALESCE(%s, lost_reason),
                        call_category = COALESCE(%s, call_category),
                        call_subcategory = COALESCE(%s, call_subcategory)
                    WHERE id = %s;
                """, (
                    new_manager, new_city, sale_status, order_sum, has_upsell, upsell_sum, upsell_text, service_booking, service_date,
                    data.client_name, data.car_model, data.tire_brand, data.tire_size, data.tire_season, data.tire_count,
                    data.branch_address, data.payment_method, data.lost_reason,
                    data.call_category, data.call_subcategory,
                    call_id
                ))

                # 3. Логируем изменения менеджера
                if old_manager != new_manager:
                    cur.execute("""
                        INSERT INTO call_audit_logs (call_id, unique_id, field_name, old_value, new_value, changed_by, comment)
                        VALUES (%s, %s, 'manager_name', %s, %s, %s, %s);
                    """, (call_id, unique_id, old_manager, new_manager, changed_by, comment))

                # 4. Логируем изменения города
                if old_city != new_city:
                    cur.execute("""
                        INSERT INTO call_audit_logs (call_id, unique_id, field_name, old_value, new_value, changed_by, comment)
                        VALUES (%s, %s, 'city_name', %s, %s, %s, %s);
                    """, (call_id, unique_id, old_city, new_city, changed_by, comment))

                # 5. Логируем изменения продажи / статуса
                if old_sale != sale_status:
                    cur.execute("""
                        INSERT INTO call_audit_logs (call_id, unique_id, field_name, old_value, new_value, changed_by, comment)
                        VALUES (%s, %s, 'sale_status', %s, %s, %s, %s);
                    """, (call_id, unique_id, old_sale, sale_status, changed_by, comment))

                if old_order_sum != order_sum:
                    cur.execute("""
                        INSERT INTO call_audit_logs (call_id, unique_id, field_name, old_value, new_value, changed_by, comment)
                        VALUES (%s, %s, 'order_sum', %s, %s, %s, %s);
                    """, (call_id, unique_id, old_order_sum, order_sum, changed_by, comment))

                if old_service != service_booking:
                    cur.execute("""
                        INSERT INTO call_audit_logs (call_id, unique_id, field_name, old_value, new_value, changed_by, comment)
                        VALUES (%s, %s, 'service_booking', %s, %s, %s, %s);
                    """, (call_id, unique_id, old_service, service_booking, changed_by, comment))

                conn.commit()

        # 6. Мгновенно синхронизируем данные
        sync_data_if_needed(force=True)

        return CallUpdateResponse(
            success=True,
            message=f"Данные по звонку #{call_id} (включая результат сделки и услуги) успешно сохранены.",
            call_id=call_id,
            manager_name=new_manager,
            city_name=new_city,
            sale_status=sale_status,
            order_sum=order_sum,
            has_upsell=has_upsell,
            service_booking=service_booking,
            client_name=data.client_name,
            tire_brand=data.tire_brand,
            tire_size=data.tire_size
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка обновления данных звонка: {e}")
