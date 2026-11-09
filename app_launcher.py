"""Точка входа для упакованной exe-версии (PyInstaller, см.
записи разработки). НЕ используется при обычном
запуске через `streamlit run app/streamlit_app.py` (см. описание проекта) —
только для сборки `BudgetPIR.exe`/`build_release.bat`.

Streamlit-скрипт нельзя просто импортировать и исполнить напрямую —
он ожидает быть запущенным через `streamlit run`, которое поднимает
ScriptRunner и веб-сервер. Поэтому этот файл программно вызывает
`streamlit.web.cli.main()` с теми же аргументами, что дал бы
`streamlit run`, указывая путь к `app/streamlit_app.py` внутри
бандла (через `resource_path`, учитывающую `sys._MEIPASS` — временную
папку распаковки PyInstaller-onefile).

Тема (цвета из токенов дизайн-кода, см. `.streamlit/config.toml` и `app/theme/theme_flags.json`)
передаётся явными CLI-флагами, а не бандлованием самого
`.streamlit/config.toml` — Streamlit ищет этот файл относительно
ТЕКУЩЕЙ РАБОЧЕЙ ДИРЕКТОРИИ процесса на момент запуска, а не
относительно расположения скрипта/exe, что ненадёжно при запуске
двойным щелчком (CWD не гарантирована). Явные флаги работают
одинаково независимо от CWD.

**Обработка ошибок при запуске двойным щелчком** (см.
записи разработки): при запуске из Проводника окно
консоли закрывается МГНОВЕННО при любом необработанном исключении —
пользователь физически не успевает прочитать, что пошло не так (в
отличие от контролируемого запуска из терминала/subprocess-теста, где
вывод остаётся в логе вызывающего процесса). Поэтому вся логика в
`main()` обёрнута в `try/except`: при ошибке — полный traceback
пишется в `BudgetPIR_error.log` РЯДОМ С EXE (не во временную папку
распаковки — та же причина, что и у `core/persistence.py::
_default_db_path`), плюс `input("Нажмите Enter для выхода...")`,
чтобы окно консоли не закрылось раньше, чем оператор успеет
прочитать сообщение на экране."""

import json
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser

ERROR_LOG_NAME = "BudgetPIR_error.log"


def _find_free_port() -> int:
    """Порт для сервера — ВСЕГДА через `socket.bind(('', 0))` (порт
    случайно выделяет ОС), НЕ фиксированный 8501.

    Раньше сначала пробовался фиксированный 8501, и только при занятости
    — случайный порт от ОС. Оказалось, что это создаёт ИМЕННО ТУ гонку,
    которую и должно было решать: если два экземпляра стартуют почти
    одновременно (например, второй — пока первый ещё распаковывает
    onefile-бандл и не успел реально забиндить порт), ОБА видят 8501
    свободным на момент проверки и оба целятся в ОДИН И ТОТ ЖЕ порт —
    у одного `streamlit` при реальном старте падает с ошибкой биндинга
    (подтверждено живым тестом с двумя exe-экземплярами, см.
    записи разработки).

    Повторить попытку с новым портом ВНУТРИ ТОГО ЖЕ ПРОЦЕССА не
    вариант — тоже проверено живьём: `streamlit.runtime.runtime.Runtime`
    хранит глобальный синглтон и падает с `RuntimeError: Runtime
    instance already exists!` при второй попытке `stcli.main()` в одном
    процессе, даже если первая попытка не смогла забиндить порт.

    Поэтому вместо "сначала общий предсказуемый порт, потом откат" —
    сразу случайный порт от ОС у КАЖДОГО экземпляра: два независимых
    вызова `bind(('', 0))` практически никогда не получат один и тот же
    порт (ОС выбирает из широкого диапазона, а не всегда один и тот же
    следующий), так что сам повод для гонки исчезает, а не просто
    смягчается. Теоретическая гонка (порт заняли ровно в микросекундном
    окне между освобождением сокета здесь и стартом сервера ниже)
    отсюда никуда не делась, но с общего заведомо перегруженного порта
    8501 она переходит в разряд действительно крайне маловероятной —
    Streamlit и так упадёт с понятной ошибкой, если так случится (не
    тихий сбой, см. существующую обработку в `__main__` ниже)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def resource_path(relative_path: str) -> str:
    """Путь к бандлованному файлу — работает и внутри PyInstaller
    (`sys._MEIPASS` — временная папка распаковки onefile, либо папка
    рядом с exe у onedir), и при обычном запуске python."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def _theme_flags() -> list:
    """Флаги темы (светлая «Белая сталь» и тёмная «Чёрная сталь») для `streamlit run`.
    Нет файла — без флагов: стандартная тема Streamlit, программа работает."""
    try:
        with open(resource_path(os.path.join("app", "theme", "theme_flags.json")), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def _exe_dir() -> str:
    """Папка рядом с САМИМ exe (не временная папка распаковки) — то
    же самое рассуждение, что и в `core/persistence.py::_default_db_path`:
    `__file__`/`sys._MEIPASS` в onefile-режиме указывают на временную
    папку, которая исчезает при закрытии программы, а лог ошибки
    должен пережить закрытие, чтобы его вообще можно было прочитать/
    переслать разработчику."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _port_file_path() -> str:
    # По PID — не по фиксированному имени: несколько экземпляров
    # запущены из ТОЙ ЖЕ папки (та же копия exe), общий файл без PID
    # в имени они бы перезаписывали друг у друга.
    return os.path.join(_exe_dir(), f"BudgetPIR_port_{os.getpid()}.txt")


def _write_port_file(port: int) -> None:
    """Публикует выбранный порт в файл рядом с exe — при запуске
    двойным щелчком (`os.startfile`) нет способа снаружи узнать порт
    иначе (в отличие от `subprocess.Popen` с перехватом stdout):
    несколько экземпляров могут получить РАЗНЫЕ порты (см.
    `_find_free_port`), а прочитать код возврата/консоль запущенного
    таким образом процесса нельзя. Файл именован по PID — не
    перетирается другим экземпляром той же программы. Не критично,
    если запись не удалась (`OSError`) — сама программа при этом
    продолжает работать нормально, это только вспомогательная
    диагностика."""
    try:
        with open(_port_file_path(), "w", encoding="utf-8") as f:
            f.write(str(port))
    except OSError:
        pass


def _remove_port_file() -> None:
    try:
        os.remove(_port_file_path())
    except OSError:
        pass


def _write_error_log_and_wait(exc_text: str) -> None:
    log_path = os.path.join(_exe_dir(), ERROR_LOG_NAME)
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(exc_text)
    except OSError:
        log_path = None  # не удалось записать — покажем хотя бы на экране

    print("Произошла ошибка при запуске BudgetPIR:")
    print(exc_text)
    if log_path:
        print(f"Подробности сохранены в: {log_path}")
    try:
        input("Нажмите Enter для выхода...")
    except EOFError:
        # stdin не подключён к интерактивной консоли (например, запуск
        # через subprocess без выделенного терминала) — не даём этому
        # заслонить УЖЕ записанную в лог настоящую ошибку вторым,
        # самостоятельным крэшем; при реальном двойном клике в
        # Проводнике stdin — обычная консоль, EOFError здесь не
        # возникает (обнаружено при диагностике, см.
        # записи разработки).
        pass


def _open_browser_when_ready(url: str, timeout: float = 30.0) -> None:
    """Открывает браузер, как только сервер реально начал отвечать —
    не сразу (сервер поднимается не мгновенно), не по фиксированной
    задержке (может быть мало или неоправданно много)."""
    import urllib.error
    import urllib.request

    health_url = url.rstrip("/") + "/_stcore/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.5)


def main() -> int:
    from streamlit.web import cli as stcli

    # НЕ повторяем попытку старта внутри этого же процесса при неудаче
    # биндинга — ПРОВЕРЕНО ЖИВЬЁМ (см. записи разработки,
    #), что так делать нельзя:
    # `streamlit.runtime.runtime.Runtime` — процесс-глобальный синглтон,
    # вторая попытка `stcli.main()` в том же процессе падает с
    # `RuntimeError: Runtime instance already exists!`, даже если первая
    # попытка не смогла забиндить порт. Сам повод для повтора убран на
    # уровне выбора порта — см. докстринг `_find_free_port()` про переход
    # на случайный порт от ОС вместо общего фиксированного 8501.
    port = _find_free_port()
    url = f"http://localhost:{port}"
    _write_port_file(port)
    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    sys.argv = [
        "streamlit",
        "run",
        resource_path(os.path.join("app", "streamlit_app.py")),
        "--server.headless=true",
        f"--server.port={port}",
        # Тема — дизайн-код компании: те же настройки, что .streamlit/config.toml (exe его не
        # видит — запускается не из папки проекта), флагами из app/theme/theme_flags.json
        # (генерирует app/theme/build_theme.py из tokens.json).
        *_theme_flags(),
        # Streamlit сам решает, что "developmentMode" включён, если
        # "site-packages" не встречается в streamlit/config.py::__file__
        # (см. streamlit/config.py::_global_development_mode) — внутри
        # PyInstaller-бандла путь всегда указывает на временную папку
        # распаковки, эта проверка там ложно срабатывает всегда.
        # developmentMode=true конфликтует с --server.port (падает с
        # RuntimeError) — принудительно выключаем явным флагом.
        "--global.developmentMode=false",
    ]
    try:
        return stcli.main()
    finally:
        # Обычный путь очистки — если сервер завершился штатно
        # (Ctrl+C/graceful shutdown, а не принудительное убийство
        # процесса извне, при котором finally не успевает сработать —
        # тогда файл порта останется, что не критично, просто
        # устаревшая диагностика).
        _remove_port_file()


if __name__ == "__main__":
    try:
        exit_code = main()
    except SystemExit as exc:
        # штатный код завершения (0/None) пропускаем молча — не ошибка;
        # ненулевой код (например streamlit сам не смог запуститься,
        # скажем, порт занят) — тоже считаем ошибкой и логируем, как и
        # обычное исключение ниже
        if exc.code in (0, None):
            raise
        _write_error_log_and_wait(f"Программа завершилась с кодом {exc.code!r}.\n")
        sys.exit(exc.code)
    except BaseException:
        _write_error_log_and_wait(traceback.format_exc())
        sys.exit(1)
    else:
        sys.exit(exit_code)
