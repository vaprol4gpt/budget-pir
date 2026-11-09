@echo off
setlocal enabledelayedexpansion

rem Собирает готовый BudgetPIR.exe для отправки на обзор (двойной
rem клик, без установки Python на целевой машине). См.
rem записи разработки за рецептом/развилками.

cd /d "%~dp0"

echo === BudgetPIR release build ===
echo.

rem Окружение: .venv (создаёт setup.bat) или, если её нет, прежняя venv.
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=venv\Scripts\python.exe"

rem Дата в формате YYYY-MM-DD не зависит от локали cmd.exe (%date%
rem форматируется по-разному на разных машинах) — берём через
rem PowerShell явно.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i

rem Зависший процесс от предыдущего запуска (BudgetPIR.exe из dist\,
rem или уже датированная копия вида BudgetPIR_YYYY-MM-DD.exe из
rem releases\ — теперь программу можно открывать несколько раз
rem одновременно, но при СБОРКЕ старый экземпляр всё ещё мешает:
rem блокирует файл на перезапись что на шаге сборки, что на шаге
rem копирования в releases\) — уже дважды приводил к PermissionError
rem при сборке (см. записи разработки).
rem
rem PowerShell Get-Process/Stop-Process, а НЕ taskkill — при проверке
rem `taskkill /F /IM "BudgetPIR*.exe"` в среде разработки НЕ находил
rem реально запущенные процессы с таким именем (не по вине самого
rem `.bat` — видимо, различие в том, какие Windows API разным
rem инструментам доступны в конкретном окружении), тогда как
rem `Get-Process`/`Stop-Process` находили и останавливали их
rem надёжно — эмпирически проверено, см. записи разработки.
rem Маска имени (`-like 'BudgetPIR*'`) ловит и "BudgetPIR" (из
rem dist\BudgetPIR.exe), и "BudgetPIR_2026-09-21" (датированную копию
rem из releases\ — Windows называет процесс по имени файла без
rem расширения, без ".exe").
echo [0/5] Остановка зависших процессов BudgetPIR...
for /f %%i in ('powershell -NoProfile -Command "(Get-Process | Where-Object { $_.ProcessName -like 'BudgetPIR*' }).Count"') do set STALE_COUNT=%%i
if "%STALE_COUNT%"=="0" (
    echo       Зависших процессов BudgetPIR не найдено.
) else (
    echo       Найдено зависших процессов: %STALE_COUNT% — останавливаю...
    powershell -NoProfile -Command "Get-Process | Where-Object { $_.ProcessName -like 'BudgetPIR*' } | Stop-Process -Force -ErrorAction SilentlyContinue"
    echo       Остановлено.
)
echo.

echo [1/5] Пересборка data\references.sqlite из reference_data\...
"%PY%" data\build_references.py
if errorlevel 1 (
    echo FAILED: не удалось пересобрать references.sqlite
    exit /b 1
)
echo.

rem Архив себестоимости: настоящий (данные компании) — только если он есть в этой папке;
rem в публичной копии и демо-сборке его нет — программа берёт демонстрационный.
set "ARCHIVE_ARG="
if exist "reference_data\archive_unit_costs.json" set "ARCHIVE_ARG=--add-data reference_data/archive_unit_costs.json;reference_data"
if defined ARCHIVE_ARG (echo       Архив себестоимости: настоящий) else (echo       Архив себестоимости: только демонстрационный)
echo [2/5] Сборка BudgetPIR.exe (PyInstaller --onefile)...
"%PY%" -m PyInstaller --onefile --name BudgetPIR --noconfirm ^
    --add-data "app/*.py;app" ^
    --add-data "app/*.json;app" ^
    --add-data "app/app_pages;app/app_pages" ^
    --add-data "app/theme;app/theme" ^
    --add-data "core;core" ^
    --add-data "modules;modules" ^
    --add-data "data/references.sqlite;data" ^
    --add-data "data/norms;data/norms" ^
    --add-data "data/pravila_parametrov.json;data" ^
    --add-data "reference_data/archive_unit_costs.demo.json;reference_data" ^
    %ARCHIVE_ARG% ^
    --hidden-import openpyxl ^
    --collect-all streamlit ^
    app_launcher.py
if errorlevel 1 (
    echo FAILED: сборка PyInstaller не удалась
    exit /b 1
)
echo.

rem [3/5] — быстрая HTTP-проверка ("сервер жив") НЕДОСТАТОЧНА сама по
rem себе: GET "/" отдаёт только статическую HTML-оболочку, а
rem streamlit_app.py реально исполняется только при настоящей
rem WebSocket-сессии — см. verify_release.py и
rem записи разработки за тем, как это было обнаружено
rem (ModuleNotFoundError на реальном запуске, незамеченный всеми
rem прежними HTTP-only смоук-тестами).
echo [3/5] Проверка, что программа реально запускается (не только отвечает на HTTP)...
"%PY%" verify_release.py dist\BudgetPIR.exe
if errorlevel 1 (
    echo FAILED: программа собралась, но не проходит проверку реального запуска
    exit /b 1
)
echo.

echo [4/5] Копирование в releases\...
if not exist releases mkdir releases

set RELEASE_FILE=releases\BudgetPIR_%TODAY%.exe
rem Прежние сборки не перезаписываем: если файл за сегодня уже есть — к имени добавляется время.
if exist "%RELEASE_FILE%" (
    for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format HHmm"') do set NOWHM=%%i
    set RELEASE_FILE=releases\BudgetPIR_%TODAY%_!NOWHM!.exe
)
copy /Y dist\BudgetPIR.exe "%RELEASE_FILE%" >nul
if errorlevel 1 (
    echo FAILED: не удалось скопировать exe в releases\
    exit /b 1
)

echo.
echo === Готово ===
echo Файл для отправки: %RELEASE_FILE%
echo Один файл, без установки Python на компьютере получателя.
echo.
echo ВАЖНО: автоматическая проверка [3/5] подтверждает, что
echo streamlit_app.py реально запускается без ошибок НА МОМЕНТ СБОРКИ.
echo Она НЕ заменяет ручную проверку интерфейса — перед отправкой
echo лучше лично открыть exe и пощёлкать по интерфейсу хотя бы один раз.

endlocal
