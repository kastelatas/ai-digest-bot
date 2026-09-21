@echo off
rem Обёртка для Планировщика заданий: python cli.py <команда> с логом в logs\<команда>.log
rem Пример: scripts\run_task.bat fetch
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
rem UTF-8, иначе кириллица в логах/print падает на кодовой странице консоли
set PYTHONUTF8=1
"venv\Scripts\python.exe" cli.py %* >> "logs\%~1.log" 2>&1
exit /b %ERRORLEVEL%
