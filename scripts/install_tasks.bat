@echo off
rem Регистрирует задачи Планировщика заданий (сам запросит права администратора).
rem Повторный запуск безопасен: задачи перезаписываются. Удалить: install_tasks.bat /uninstall
chcp 65001 >nul
set FLAG=
if /i "%~1"=="/uninstall" set FLAG=-Uninstall
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_tasks.ps1" %FLAG%
pause
