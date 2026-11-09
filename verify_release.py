"""Проверка собранного `BudgetPIR.exe`, реально исполняющая
`app/streamlit_app.py` на сервере — не просто "сервер отвечает".

ПОЧЕМУ ЭТОТ СКРИПТ ВООБЩЕ ПОЯВИЛСЯ (см. записи разработки,
диагностика "ModuleNotFoundError... magic_funcs" на реальном запуске):
до этого все смоук-тесты проверяли только `GET /` и
`GET /_stcore/health`. Оба этих запроса — обычные HTTP-запросы,
отдающие статическую HTML-оболочку и лёгкий readiness-чек
соответственно; НИ ОДИН из них не запускает `streamlit_app.py`.
Streamlit реально исполняет скрипт оператора ТОЛЬКО когда клиент
открывает WebSocket-сессию на `/_stcore/stream` И присылает
`BackMsg` с `rerun_script` (это обычно делает JS-фронтенд сразу при
подключении) — без этого второго шага сессия создаётся, но скрипт
не запускается вообще (проверено: пассивная WebSocket-сессия без
`rerun_script` не порождает НИ ОДНОГО сообщения от сервера в течение
20 секунд ожидания). Поэтому HTTP-проверки могли (и на практике
показали) 200 OK, пока реальный запуск скрипта был сломан
(`ModuleNotFoundError`) — они физически не могли поймать эту ошибку.

Этот скрипт замыкает именно тот пробел: подключается по WebSocket,
шлёт `rerun_script`, читает поток `ForwardMsg` и ищет в сырых байтах
узнаваемые признаки ошибки (протобуф сериализует строковые поля как
обычный UTF-8, поэтому текст traceback'а виден в сырых байтах без
полного разбора протобуф-схемы ForwardMsg — не нужно тащить
ForwardMsg_pb2 и разбирать вложенные Delta/Exception-сообщения)."""

import glob
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ERROR_MARKERS = ("Traceback", "ModuleNotFoundError", "ImportError", "Exception")


def _wait_for_port_file(exe_dir: str, known_before: set[str], timeout: float) -> int | None:
    """Ждёт появления НОВОГО файла `BudgetPIR_port_<PID>.txt` рядом с
    exe — с тех пор, как `app_launcher.py` перешёл на случайный порт
    от ОС при КАЖДОМ запуске (см. записи разработки), фиксированный `--port 8501` по умолчанию здесь
    больше не годится: нечего было бы слушать. PID в имени файла
    может не совпадать с PID процесса-обёртки из `subprocess.Popen`
    ниже (PyInstaller-onefile bootloader порождает отдельный дочерний
    процесс, который реально исполняет код) — поэтому ищем по
    ПОЯВЛЕНИЮ нового файла, а не по конкретному PID."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = set(glob.glob(os.path.join(exe_dir, "BudgetPIR_port_*.txt")))
        new_files = current - known_before
        if new_files:
            path = next(iter(new_files))
            try:
                with open(path, encoding="utf-8") as f:
                    return int(f.read().strip())
            except (OSError, ValueError):
                pass
        time.sleep(0.5)
    return None


def _wait_for_http_ok(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)
    return False


async def _check_script_actually_runs(port: str, timeout: float) -> tuple[bool, list[str]]:
    import asyncio

    import websockets
    from streamlit.proto.BackMsg_pb2 import BackMsg

    found_markers: list[str] = []
    got_any_message = False

    url = f"ws://localhost:{port}/_stcore/stream"
    async with websockets.connect(url, open_timeout=10) as ws:
        back_msg = BackMsg()
        back_msg.rerun_script.SetInParent()
        await ws.send(back_msg.SerializeToString())

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            remaining = max(0.5, deadline - asyncio.get_event_loop().time())
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            got_any_message = True
            text = msg if isinstance(msg, str) else msg.decode("utf-8", errors="ignore")
            for marker in ERROR_MARKERS:
                if marker in text and marker not in found_markers:
                    found_markers.append(marker)

    return got_any_message, found_markers


def main() -> int:
    if len(sys.argv) < 2:
        print("Использование: verify_release.py <путь к exe> [порт]")
        return 2

    exe_path = sys.argv[1]
    exe_dir = os.path.dirname(os.path.abspath(exe_path)) or "."
    error_log_path = os.path.join(exe_dir, "BudgetPIR_error.log")
    known_port_files = set(glob.glob(os.path.join(exe_dir, "BudgetPIR_port_*.txt")))

    # Порт больше НЕ фиксирован (был жёстко 8501 — сломалось, когда
    # app_launcher.py перешёл на случайный порт от ОС при каждом
    # запуске, см. докстринг `_wait_for_port_file`) — необязательный
    # аргумент оставлен только для явной ручной проверки конкретного
    # уже запущенного порта, обычным путём порт узнаём из
    # объявляющего файла рядом с exe.
    explicit_port = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Запуск {exe_path} для проверки...")
    # НЕ subprocess.PIPE для stdout/stderr: PyInstaller-onefile
    # bootloader порождает отдельный дочерний процесс, который
    # реально держит открытым конец пайпа — `proc.terminate()`/
    # `proc.kill()` на PID обёртки не гарантированно завершает и его,
    # и тогда блокирующий `proc.stdout.read()` в `finally` НИКОГДА не
    # получает EOF (реально воспроизведено: зависание на много минут
    # при живой проверке, см. записи разработки,
    #). Диагностика на случай падения — не захваченный
    # stdout, а `BudgetPIR_error.log` рядом с exe (программа и так
    # пишет туда при необработанном исключении).
    proc = subprocess.Popen([exe_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok_overall = False
    try:
        if explicit_port is not None:
            port = explicit_port
        else:
            print("[0/3] Ожидание файла с объявленным портом...")
            discovered = _wait_for_port_file(exe_dir, known_port_files, timeout=30)
            if discovered is None:
                print("FAILED: файл BudgetPIR_port_*.txt не появился за 30 секунд")
                return 1
            port = str(discovered)
            print(f"      OK — порт {port}.")

        health_url = f"http://localhost:{port}/_stcore/health"
        root_url = f"http://localhost:{port}/"

        print("[1/3] Ожидание HTTP-ответа /_stcore/health...")
        if not _wait_for_http_ok(health_url, timeout=30):
            print("FAILED: сервер не поднялся за 30 секунд")
            return 1
        print("      OK — сервер отвечает.")

        print("[2/3] Проверка корневого / (статическая оболочка)...")
        if not _wait_for_http_ok(root_url, timeout=10):
            print("FAILED: корневой путь не отвечает 200 (нет статики?)")
            return 1
        print("      OK.")

        print(
            "[3/3] РЕАЛЬНЫЙ запуск скрипта через WebSocket-сессию "
            "(/_stcore/stream + rerun_script BackMsg)..."
        )
        import asyncio

        got_any, markers = asyncio.run(_check_script_actually_runs(port, timeout=20))

        if not got_any:
            print(
                "FAILED: WebSocket-сессия открылась, но сервер не прислал "
                "НИ ОДНОГО сообщения за 20 секунд после rerun_script — "
                "скрипт, похоже, не выполняется вовсе."
            )
            return 1

        if markers:
            print(f"FAILED: в потоке ответа сервера найдены признаки ошибки: {markers}")
            print(
                "Это означает, что streamlit_app.py реально запускается, "
                "но падает при выполнении (например, отсутствующий модуль "
                "в сборке) — проверьте вручную, открыв exe."
            )
            return 1

        print("      OK — сервер прислал сообщения выполнения скрипта, признаков ошибки не найдено.")
        ok_overall = True
        return 0
    finally:
        if proc.poll() is None:
            # `taskkill /T` — убивает ВСЕ дерево процессов по PID
            # (обёртка bootloader + реально исполняющий дочерний
            # процесс), а не только сам `proc.pid` — обычные
            # `proc.terminate()`/`proc.kill()` этого не делали и
            # оставляли дочерний процесс висеть, что и вызывало
            # зависание раньше (см. комментарий у `subprocess.Popen`
            # выше). `/F` — принудительно, без ожидания штатного
            # завершения.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
        if not ok_overall and os.path.exists(error_log_path):
            try:
                with open(error_log_path, encoding="utf-8") as f:
                    print("\n--- BudgetPIR_error.log ---")
                    print(f.read()[-3000:])
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
