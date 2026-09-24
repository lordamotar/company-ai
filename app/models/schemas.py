from pydantic import BaseModel, Field
from typing import Optional

class CallUpdateRequest(BaseModel):
    call_id: int = Field(..., description="ID звонка в базе данных", examples=[15])
    manager_name: str = Field(default="Оператор", description="Имя менеджера", examples=["Жандос"])
    city_name: str = Field(default="Алматы", description="Город или филиал", examples=["Алматы"])
    changed_by: str = Field(default="Супервайзер", description="Инициатор изменения", examples=["Супервайзер"])
    comment: str = Field(default="Ручная корректировка данных", description="Комментарий к изменению", examples=["Коррекция распознавания"])
    sale_status: Optional[str] = Field(default=None, description="Статус сделки/продажи", examples=["Продажа состоялась"])
    order_sum: Optional[str] = Field(default=None, description="Сумма заказа", examples=["87 000 ₸"])
    has_upsell: Optional[bool] = Field(default=None, description="Была ли совершена допродажа", examples=[True])
    upsell_sum: Optional[str] = Field(default=None, description="Сумма допродажи", examples=["18 000 ₸"])
    upsell_text: Optional[str] = Field(default=None, description="Что допродано", examples=["Сезонное хранение шин"])
    service_booking: Optional[str] = Field(default=None, description="Запись на услугу", examples=["Шиномонтаж"])
    service_date: Optional[str] = Field(default=None, description="Дата записи на услугу", examples=["15.10.2020 15:00"])
    client_name: Optional[str] = Field(default=None, description="Имя клиента", examples=["Канапьянов Елдос"])
    car_model: Optional[str] = Field(default=None, description="Автомобиль", examples=["Toyota Camry"])
    tire_brand: Optional[str] = Field(default=None, description="Бренд шин", examples=["BFGoodrich"])
    tire_size: Optional[str] = Field(default=None, description="Типоразмер шин", examples=["215/70 R16"])
    tire_season: Optional[str] = Field(default=None, description="Сезонность", examples=["Зимние шипованные"])
    tire_count: Optional[str] = Field(default=None, description="Количество", examples=["4 шт."])
    branch_address: Optional[str] = Field(default=None, description="Филиал/склад", examples=["Карагайлы"])
    payment_method: Optional[str] = Field(default=None, description="Способ оплаты", examples=["Kaspi QR"])
    lost_reason: Optional[str] = Field(default=None, description="Причина отказа", examples=["Нет нужного размера"])
    call_category: Optional[str] = Field(default=None, description="Категория звонка", examples=["Запись на шиномонтаж"])
    call_subcategory: Optional[str] = Field(default=None, description="Подкатегория звонка", examples=["Сервисное обслуживание"])

class CallUpdateResponse(BaseModel):
    success: bool
    message: str
    call_id: int
    manager_name: str
    city_name: str
    sale_status: Optional[str] = None
    order_sum: Optional[str] = None
    has_upsell: Optional[bool] = None
    service_booking: Optional[str] = None
    client_name: Optional[str] = None
    tire_brand: Optional[str] = None
    tire_size: Optional[str] = None

class HealthCheckResponse(BaseModel):
    status: str
    version: str
    database: str
