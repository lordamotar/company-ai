"""
Скрипт для возврата файлов с ошибками из папки error/ обратно в очередь incoming/.
Сбрасывает статус звонков в БД (status='new', retry_count=0, last_error=NULL),
позволяя запустить повторный анализ.
"""
import os
import sys
import logging
from worker import return_errors_to_incoming

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

if __name__ == "__main__":
    count = return_errors_to_incoming()
    if count == 0:
        logging.info("Папка error/ пуста. Нет файлов для возврата в очередь.")
    else:
        logging.info(f"Успешно возвращено файлов: {count}. Они готовы к повторной обработке воркером.")
