import os
import sys
import time
import http.server
import socketserver
from export_db_to_json import export_calls_to_json

PORT = 8080
DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "html")

# Дебаунс экспорта, чтобы не спамить БД при частых запросах браузера
_LAST_EXPORT_TIME = 0

class ThreadingHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

class RobustHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        global _LAST_EXPORT_TIME
        now = time.time()
        # Обновляем данные из БД не чаще одного раза в 2 секунды
        if (self.path.startswith("/calls_data.json") or self.path == "/" or self.path.startswith("/?")) and (now - _LAST_EXPORT_TIME > 2.0):
            try:
                _LAST_EXPORT_TIME = now
                export_calls_to_json()
            except Exception as e:
                print(f"[!] Ошибка экспорта из БД: {e}")
        try:
            return super().do_GET()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # Нормальное отключение браузера (перезагрузка/смена вкладки)
            pass

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def log_message(self, format, *args):
        # Компактный лог без мусора
        if args and str(args[0]).startswith("200") and "calls_data.json" in str(self.path):
            return
        super().log_message(format, *args)

def run_server():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    try:
        export_calls_to_json()
    except Exception as e:
        print(f"[!] Начальный экспорт: {e}")

    server = ThreadingHTTPServer(("", PORT), RobustHandler)
    url = f"http://localhost:{PORT}"
    print("=" * 60)
    print(f"🚀 AIKOS CRM Сервер аналитики запущен на: {url}")
    print(f"📁 Обслуживаемая папка: {DIRECTORY}")
    print("💡 Многопоточный режим: устойчив к разрывам соединений и перезагрузкам страниц.")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()

if __name__ == "__main__":
    run_server()
