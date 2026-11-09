@echo off
setlocal
chcp 65001 >nul

rem setup.bat — подготовка проекта на новом ПК после git clone:
rem .venv, зависимости, справочник расценок, тесты, итог.
rem Повторный запуск безопасен: существующая .venv переиспользуется.
rem Параметр /nopause — не ждать нажатия клавиши в конце (для автоматики).

cd /d "%~dp0"

set "RESULT=ОШИБКА"
set "STEP="

echo === BUDGET_PIR: установка ===
echo.

rem [1] Python 3.13: сначала лаунчер py, иначе python из PATH
set "STEP=поиск Python 3.13"
set "PYBASE="
py -3.13 -c "import sys" >nul 2>&1 && set "PYBASE=py -3.13"
if not defined PYBASE (
    python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,13) else 1)" >nul 2>&1 && set "PYBASE=python"
)
if not defined PYBASE (
    echo Не найден Python 3.13. Установите его с python.org и запустите setup.bat снова.
    goto :end
)
echo [1/5] Python: %PYBASE%

rem [2] Виртуальное окружение .venv
set "STEP=создание .venv"
if not exist ".venv\Scripts\python.exe" (
    echo [2/5] Создаю .venv...
    %PYBASE% -m venv .venv
    if errorlevel 1 goto :end
) else (
    echo [2/5] .venv уже есть — использую её
)
set "PY=.venv\Scripts\python.exe"

rem [3] Зависимости: точные версии ВСЕХ пакетов (рантайм + pytest +
rem     PyInstaller и их зависимости) из requirements-lock.txt
set "STEP=установка зависимостей"
echo [3/5] Устанавливаю зависимости из requirements-lock.txt...
%PY% -m pip install --disable-pip-version-check -q -r requirements-lock.txt
if errorlevel 1 goto :end
rem Сам проект — в .venv в режиме редактирования (pyproject.toml): пакеты
rem core/ и modules/ импортируются из любой папки, в т.ч. при запуске
rem просто "streamlit run". Без сети: зависимости уже стоят, setuptools —
rem из requirements-lock.txt.
%PY% -m pip install --disable-pip-version-check -q --no-deps --no-build-isolation -e .
if errorlevel 1 goto :end

rem Кириллические имена файлов в выводе git — без экранирования
git config --local core.quotepath false >nul 2>&1

rem [4] Справочник расценок — в git не хранится, собирается из reference_data\
set "STEP=сборка справочника data\references.sqlite"
echo [4/5] Собираю справочник расценок...
%PY% data\build_references.py >nul
if errorlevel 1 goto :end

rem [5] Тесты
set "STEP=тесты"
echo [5/5] Запускаю тесты...
%PY% -m pytest -q
if errorlevel 1 goto :end

set "RESULT=ГОТОВО"
set "STEP="

:end
echo.
echo === Итог: %RESULT% ===
if defined STEP echo Остановлено на шаге: %STEP%
if "%RESULT%"=="ГОТОВО" (
    for /f "delims=" %%v in ('.venv\Scripts\python.exe --version') do echo %%v в .venv
    echo Запуск программы: launch_app.bat
)
if /i not "%~1"=="/nopause" pause
if "%RESULT%"=="ГОТОВО" (exit /b 0) else (exit /b 1)
