FROM python:3.12-slim

WORKDIR /app

# Копируем легковесные зависимости веб-сервера
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# Копируем исходный код приложения
COPY . /app

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
