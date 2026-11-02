@echo off
title Get the WritersBot window
if not exist "%~dp0window-setup.ps1" (
    echo The rest of the folder is not here. Right-click the zip file, choose Extract All, and run this from the new folder.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0window-setup.ps1"
if errorlevel 1 pause
