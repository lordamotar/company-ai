import os
import sys
import time
import http.server
import socketserver

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import json
import shutil
import urllib.parse
import psycopg2
from export_db_to_json import export_calls_to_json, export_audit_logs_to_json, DB_CONFIG

PORT = 8080
BASE_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DIRECTORY = os.path.join(BASE_PROJECT_DIR, "html")

AUDIO_DIRS = [
    os.path.join(BASE_PROJECT_DIR, "incoming"),
    os.path.join(BASE_PROJECT_DIR, ".tech", "call"),
    os.path.join(BASE_PROJECT_DIR, "processed"),
    os.path.join(BASE_PROJECT_DIR, "processing"),
]

def find_audio_file(call_id=None, source_file=None, unique_id=None, phone=None):
    # 1. Если передан call_id, получаем инфу из базы
    if call_id:
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()
            cur.execute("SELECT source_file, unique_id, phone FROM call_transcriptions WHERE id = %s;", (call_id,))
            row = cur.fetchone()
            if row:
                if not source_file and row[0]: source_file = row[0]
                if not unique_id and row[1]: unique_id = row[1]
                if not phone and row[2]: phone = row[2]
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[!] Ошибка БД при поиске аудио: {e}")

    # 2. Ищем прямо по source_file
    if source_file:
        base_name = os.path.basename(source_file)
        for adir in AUDIO_DIRS:
            candidate = os.path.join(adir, base_name)
            if os.path.isfile(candidate):
                return candidate

    # 3. Ищем по unique_id
    if unique_id:
        for adir in AUDIO_DIRS:
            if not os.path.isdir(adir): continue
            for fname in os.listdir(adir):
                if unique_id in fname and fname.lower().endswith(('.wav', '.mp3', '.ogg', '.m4a')):
                    return os.path.join(adir, fname)

    # 4. Ищем по номеру телефона
    if phone:
        clean_phone = "".join(filter(str.isdigit, phone))
        if len(clean_phone) >= 7:
            for adir in AUDIO_DIRS:
                if not os.path.isdir(adir): continue
                for fname in os.listdir(adir):
                    if clean_phone in fname and fname.lower().endswith(('.wav', '.mp3', '.ogg', '.m4a')):
                        return os.path.join(adir, fname)

    return None

# Дебаунс экспорта, чтобы не спамить БД при частых запросах браузера
_LAST_EXPORT_TIME = 0

class ThreadingHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

class RobustHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_POST(self):
        if self.path == "/api/calls/update" or self.path.startswith("/api/calls/update"):
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8")
            
            try:
                data = json.loads(post_data)
                call_id = int(data.get("call_id"))
                new_manager = data.get("manager_name", "").strip() or "Оператор"
                new_city = data.get("city_name", "").strip() or "Алматы"
                changed_by = data.get("changed_by", "").strip() or "Супервайзер"
                comment = data.get("comment", "").strip() or "Ручная корректировка данных"

                conn = psycopg2.connect(**DB_CONFIG)
                cur = conn.cursor()

                # Получаем старые данные
                cur.execute("SELECT manager_name, city_name FROM call_transcriptions WHERE id = %s;", (call_id,))
                row = cur.fetchone()
                old_manager = row[0] if row and row[0] else "Оператор"
                old_city = row[1] if row and row[1] else "Алматы"

                # Обновляем в call_transcriptions
                cur.execute("""
                    UPDATE call_transcriptions 
                    SET manager_name = %s, city_name = %s 
                    WHERE id = %s;
                """, (new_manager, new_city, call_id))

                # Логируем изменения менеджера
                if old_manager != new_manager:
                    cur.execute("""
                        INSERT INTO call_audit_logs (call_id, field_name, old_value, new_value, changed_by, comment)
                        VALUES (%s, 'manager_name', %s, %s, %s, %s);
                    """, (call_id, old_manager, new_manager, changed_by, comment))

                # Логируем изменения города
                if old_city != new_city:
                    cur.execute("""
                        INSERT INTO call_audit_logs (call_id, field_name, old_value, new_value, changed_by, comment)
                        VALUES (%s, 'city_name', %s, %s, %s, %s);
                    """, (call_id, old_city, new_city, changed_by, comment))

                conn.commit()
                cur.close()
                conn.close()

                # Мгновенно обновляем JSON файлы
                export_calls_to_json()
                export_audit_logs_to_json()

                response = json.dumps({
                    "success": True,
                    "message": f"Данные по звонку #{call_id} успешно обновлены и зафиксированы в журнале аудита.",
                    "call_id": call_id,
                    "manager_name": new_manager,
                    "city_name": new_city
                }, ensure_ascii=False).encode("utf-8")

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(response)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(response)
                return
            except Exception as e:
                err_resp = json.dumps({"success": False, "error": str(e)}, ensure_ascii=False).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(err_resp)))
                self.end_headers()
                self.wfile.write(err_resp)
                return

        self.send_error(404, "Endpoint not found")

    def do_GET(self):
        global _LAST_EXPORT_TIME
        now = time.time()

        # Эндпоинт стриминга аудиозаписи звонка
        if self.path.startswith("/api/audio"):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            call_id_str = params.get("call_id", [None])[0]
            call_id = None
            if call_id_str:
                try:
                    call_id = int(call_id_str)
                except ValueError:
                    call_id = None

            file_param = params.get("file", [None])[0]
            unique_id = params.get("unique_id", [None])[0]
            phone = params.get("phone", [None])[0]

            audio_path = find_audio_file(call_id=call_id, source_file=file_param, unique_id=unique_id, phone=phone)

            if not audio_path or not os.path.isfile(audio_path):
                self.send_error(404, "Audio file not found")
                return

            ext = os.path.splitext(audio_path)[1].lower()
            content_type = "audio/wav" if ext == ".wav" else ("audio/mpeg" if ext == ".mp3" else "audio/ogg")
            file_size = os.path.getsize(audio_path)

            range_header = self.headers.get("Range")
            if range_header:
                try:
                    byte_range = range_header.strip().split("=")[1]
                    parts = byte_range.split("-")
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
                    if end >= file_size:
                        end = file_size - 1
                    length = end - start + 1

                    self.send_response(206)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                    self.send_header("Content-Length", str(length))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()

                    with open(audio_path, "rb") as f:
                        f.seek(start)
                        self.wfile.write(f.read(length))
                    return
                except Exception as e:
                    print(f"[!] Ошибка Range стриминга: {e}")

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            try:
                with open(audio_path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            return

        # Обновляем данные из БД не чаще одного раза в 2 секунды
        if (self.path.startswith("/calls_data.json") or self.path.startswith("/audit_data.json") or self.path == "/" or self.path.startswith("/?")) and (now - _LAST_EXPORT_TIME > 2.0):
            try:
                _LAST_EXPORT_TIME = now
                export_calls_to_json()
                export_audit_logs_to_json()
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
