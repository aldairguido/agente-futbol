@echo off
setlocal enabledelayedexpansion
title Bot Interactivo de Telegram - Agente Cuantitativo
chcp 65001 > nul

:: AUTO-LOCALIZADO: %~dp0 es la carpeta donde esta ESTE .bat.
:: Asi funciona desde C:, desde D: o desde un USB sin tocar nada.
set "PROYECTO=%~dp0"
if "%PROYECTO:~-1%"=="\" set "PROYECTO=%PROYECTO:~0,-1%"
cd /d "%PROYECTO%"

set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%PROYECTO%"

set "PYTHON_EXE=C:\Users\Aldair\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

echo ============================================================
echo   🤖 BOT INTERACTIVO DE TELEGRAM - DEMONIO DE CONTROL
echo   Directorio: %PROYECTO%
echo   Python:     %PYTHON_EXE%
echo ============================================================
echo.
echo Presiona Ctrl + C para detener el bot en cualquier momento.
echo.

"%PYTHON_EXE%" -u bot_interactivo.py

pause
