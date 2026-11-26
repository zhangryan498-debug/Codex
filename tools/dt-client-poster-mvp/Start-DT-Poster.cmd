@echo off
setlocal
chcp 65001 >nul
if not defined DT_POSTER_PROJECT set "DT_POSTER_PROJECT=%~dp0..\.."
if not defined DT_POSTER_PYTHON set "DT_POSTER_PYTHON=%DT_POSTER_PROJECT%\.venv\Scripts\python.exe"
if not defined DT_POSTER_ENGINE set "DT_POSTER_ENGINE=%DT_POSTER_PROJECT%\tools\psd-engine\engine.cjs"
if not defined DT_POSTER_NODE set "DT_POSTER_NODE=node.exe"
set "DT_POSTER_PROFILE=%~1"
if not defined DT_POSTER_PROFILE set "DT_POSTER_PROFILE=%LOCALAPPDATA%\DT\IndependentWorkbench\bava-tokenplan-new-test-design-workspace"
set "DT_POSTER_PORT=%~2"
if not defined DT_POSTER_PORT set "DT_POSTER_PORT=8735"
set "DT_POSTER_OPEN=--open-browser"
if /i "%~3"=="--no-browser" set "DT_POSTER_OPEN="
set "DT_POSTER_FOREGROUND=--foreground-token-plan"
if /i "%~4"=="--foreground-token-plan" set "DT_POSTER_FOREGROUND=--foreground-token-plan"
if not exist "%DT_POSTER_PYTHON%" goto python_missing
"%DT_POSTER_PYTHON%" -B -X utf8 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if errorlevel 1 goto python_missing
if not exist "%~dp0launch.py" (
  echo [app] D^&T launch.py is missing. Restore the complete local workbench folder.
  goto launch_failed
)
"%DT_POSTER_PYTHON%" -B -X utf8 "%~dp0launch.py" --project "%DT_POSTER_PROJECT%" --engine "%DT_POSTER_ENGINE%" --node "%DT_POSTER_NODE%" --profile "%DT_POSTER_PROFILE%" --port "%DT_POSTER_PORT%" %DT_POSTER_OPEN% %DT_POSTER_FOREGROUND%
if errorlevel 1 goto launch_failed
exit /b 0

:python_missing
echo [python] D^&T needs a working local Python 3.10 or newer with project dependencies.
echo Restore the D^&T project's .venv or set DT_POSTER_PYTHON to its local python.exe.

:launch_failed
if defined DT_POSTER_OPEN pause
exit /b 1
