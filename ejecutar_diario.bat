@echo off
setlocal enabledelayedexpansion
title Agente de Futbol - Ejecucion Diaria Automatizada
chcp 65001 > nul

:: AUTO-LOCALIZADO: %~dp0 es la carpeta donde esta ESTE .bat.
:: Asi funciona desde C:, desde D: o desde un USB sin tocar nada.
set "PROYECTO=%~dp0"
if "%PROYECTO:~-1%"=="\" set "PROYECTO=%PROYECTO:~0,-1%"
cd /d "%PROYECTO%"

set "PYTHONIOENCODING=utf-8"

:: Definir ruta de Python preferida (o fallback al comando global)
set "PYTHON_EXE=C:\Users\Aldair\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

:: Crear directorio de logs si no existe
if not exist "logs" mkdir "logs"

:: Obtener marca de tiempo universal mediante PowerShell
for /f "usebackq tokens=*" %%a in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'"`) do set "TIMESTAMP=%%a"
if "%TIMESTAMP%"=="" set "TIMESTAMP=%date%_%time%"

set "LOG_FILE=logs\ejecucion_%TIMESTAMP%.log"

echo ============================================================
echo   AGENTE DE ANALISIS DE FUTBOL - EJECUCION DIARIA
echo   Marca de tiempo: %TIMESTAMP%
echo   Directorio: %PROYECTO%
echo   Log: %LOG_FILE%
echo ============================================================
echo.

:: Ejecutar agente en modo no interactivo guardando log y mostrando progreso
"%PYTHON_EXE%" -u agente.py > "%LOG_FILE%" 2>&1

set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% EQU 0 (
    echo.
    echo ============================================================
    echo   [EXITO] Analisis diario finalizado correctamente.
    echo   Revisa el informe generado en: %PROYECTO%\informes\
    echo   Registro completo en: %LOG_FILE%
    echo ============================================================
) else (
    echo.
    echo ============================================================
    echo   [ERROR] La ejecucion termino con codigo de error %EXIT_CODE%.
    echo   Revisa los detalles del fallo en: %LOG_FILE%
    echo ============================================================
)

exit /b %EXIT_CODE%
