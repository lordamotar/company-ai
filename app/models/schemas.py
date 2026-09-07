from pydantic import BaseModel, Field
from typing import Optional

class CallUpdateRequest(BaseModel):
    call_id: int = Field(..., description="ID звонка в базе данных", examples=[15])
    manager_name: str = Field(default="Оператор", description="Имя менеджера", examples=["Жандос"])
    city_name: str = Field(default="Алматы", description="Город или филиал", examples=["Алматы"])
    changed_by: str = Field(default="Супервайзер", description="Инициатор изменения", examples=["Супервайзер"])
    comment: str = Field(default="Ручная корректировка данных", description="Комментарий к изменению", examples=["Коррекция распознавания"])

class CallUpdateResponse(BaseModel):
    success: bool
    message: str
    call_id: int
    manager_name: str
    city_name: str

class HealthCheckResponse(BaseModel):
    status: str
    version: str
    database: str
