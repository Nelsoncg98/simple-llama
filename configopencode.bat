@echo off
chcp 65001 >nul
title Sincronizar Modelos con OpenCode
cls

cd /d "%~dp0"

if exist "sync_opencode.py" (
    python sync_opencode.py
) else (
    echo [!] ERROR: No se encontró sync_opencode.py.
)

echo.
pause
