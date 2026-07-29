@echo off
setlocal
cd /d "%~dp0"
set "TWSTOCKWIDGET_SETTINGS_PATH=%~dp0settings.json"
start "" "%~dp0TWStockWidget.exe"
