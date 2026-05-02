@echo off
cd /d "%~dp0"
title vDLNA Build

echo ============================================
echo   vDLNA - PyInstaller Build Script
echo ============================================
echo.

REM Check venv
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Please create a virtual environment first.
    pause
    exit /b 1
)

set PYTHON=.venv\Scripts\python.exe
set PIP=.venv\Scripts\pip.exe

REM Check pyinstaller
%PIP% show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing pyinstaller...
    %PIP% install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install pyinstaller.
        pause
        exit /b 1
    )
)

REM Check icon
if not exist "assets\icon.ico" (
    echo [WARN] assets\icon.ico missing, exe will use default icon.
    echo.
)

REM Check spec
if not exist "vDLNA.spec" (
    echo [ERROR] vDLNA.spec not found.
    pause
    exit /b 1
)

REM Clean old build artifacts
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"

echo [INFO] Building...
echo.

%PYTHON% -m PyInstaller --clean --noconfirm vDLNA.spec

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Build complete!
echo   Output: dist\vDLNA.exe
echo ============================================
echo.

for %%F in ("dist\vDLNA.exe") do echo   Size: %%~zF bytes

echo.
pause
