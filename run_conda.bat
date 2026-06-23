@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ===============================================
echo   OrchardBridge - portable launcher
echo ===============================================
echo.

rem This launcher keeps OrchardBridge isolated in a project-local .venv.
rem The .venv is created once and reused. Dependencies are installed again only
rem when requirements.txt changes or when ORCHARD_BRIDGE_REPAIR=1 is set.
set "APP_DIR=%CD%"
set "VENV_DIR=%APP_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ_HASH_FILE=%VENV_DIR%\.requirements.sha256"
set "TEMP_HASH_FILE=%TEMP%\orchardbridge_req_%RANDOM%%RANDOM%.txt"
set "PYTHONNOUSERSITE=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_NO_WARN_SCRIPT_LOCATION=0"
set "PY_DETECT_FILE=%TEMP%\orchardbridge_python_%RANDOM%%RANDOM%.txt"

if exist "%VENV_PY%" goto :have_venv

if exist "%VENV_DIR%" (
    echo [WARN] A .venv folder exists but Python is missing inside it.
    echo [WARN] Removing the incomplete .venv and rebuilding it...
    rmdir /s /q "%VENV_DIR%" >nul 2>nul
)

echo [INFO] Creating local virtual environment: %VENV_DIR%
set "BASE_PY="

rem 1) Prefer the Windows Python launcher when available.
for %%V in (3.13 3.12 3.11 3.10 3) do (
    if not defined BASE_PY (
        py -%%V -c "import sys; print(sys.executable)" > "%PY_DETECT_FILE%" 2>nul
        if not errorlevel 1 set /p BASE_PY=<"%PY_DETECT_FILE%"
    )
)

rem 2) Search common python.org installation folders.
if not defined BASE_PY (
    for %%P in (
        "%LocalAppData%\Programs\Python\Python313\python.exe"
        "%LocalAppData%\Programs\Python\Python312\python.exe"
        "%LocalAppData%\Programs\Python\Python311\python.exe"
        "%LocalAppData%\Programs\Python\Python310\python.exe"
        "%ProgramFiles%\Python313\python.exe"
        "%ProgramFiles%\Python312\python.exe"
        "%ProgramFiles%\Python311\python.exe"
        "%ProgramFiles%\Python310\python.exe"
    ) do (
        if not defined BASE_PY if exist %%~P set "BASE_PY=%%~P"
    )
)

rem 3) Search common Conda-family locations. We use this Python only to create
rem    the local .venv; packages are not installed into conda base.
if not defined BASE_PY (
    for %%P in (
        "%ProgramData%\anaconda3\python.exe"
        "%ProgramData%\miniconda3\python.exe"
        "%ProgramData%\miniforge3\python.exe"
        "%ProgramData%\mambaforge\python.exe"
        "%UserProfile%\anaconda3\python.exe"
        "%UserProfile%\miniconda3\python.exe"
        "%UserProfile%\miniforge3\python.exe"
        "%UserProfile%\mambaforge\python.exe"
        "%LocalAppData%\anaconda3\python.exe"
        "%LocalAppData%\miniconda3\python.exe"
        "%LocalAppData%\miniforge3\python.exe"
        "%LocalAppData%\mambaforge\python.exe"
    ) do (
        if not defined BASE_PY if exist %%~P set "BASE_PY=%%~P"
    )
)

rem 4) Fall back to python on PATH.
if not defined BASE_PY (
    python -c "import sys; print(sys.executable)" > "%PY_DETECT_FILE%" 2>nul
    if not errorlevel 1 set /p BASE_PY=<"%PY_DETECT_FILE%"
)

if exist "%PY_DETECT_FILE%" del "%PY_DETECT_FILE%" >nul 2>nul

if not defined BASE_PY (
    echo [ERROR] Python was not found.
    echo.
    echo OrchardBridge needs Python 3.10+ only to create its local .venv.
    echo Supported options:
    echo   - Python 3.10+ from python.org, or
    echo   - Anaconda / Miniconda / Miniforge installed in a standard folder.
    echo.
    echo If Anaconda is installed in a custom folder, open Anaconda Prompt,
    echo cd to this OrchardBridge folder, then run: run_conda.bat
    pause
    exit /b 1
)

echo [INFO] Using Python to create venv: %BASE_PY%
"%BASE_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] The detected Python is older than 3.10: %BASE_PY%
    echo Please install Python 3.10+ or a newer Anaconda/Miniconda.
    pause
    exit /b 1
)

"%BASE_PY%" -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo [ERROR] Failed to create the local virtual environment.
    echo Try right-clicking run_conda.bat and choosing Run as administrator,
    echo or move OrchardBridge to a folder you can write to, such as Desktop.
    pause
    exit /b 1
)

:have_venv
echo [INFO] Using app-local Python: %VENV_PY%
"%VENV_PY%" --version
if errorlevel 1 (
    echo [ERROR] The local Python environment is not usable.
    echo Delete the .venv folder and run this launcher again.
    pause
    exit /b 1
)

"%VENV_PY%" -m ensurepip --upgrade >nul 2>nul
"%VENV_PY%" -c "import hashlib, pathlib; print(hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest())" > "%TEMP_HASH_FILE%" 2>nul
if errorlevel 1 (
    echo [WARN] Could not hash requirements.txt; dependency check will run.
    set "REQ_HASH="
) else (
    set /p REQ_HASH=<"%TEMP_HASH_FILE%"
)
if exist "%TEMP_HASH_FILE%" del "%TEMP_HASH_FILE%" >nul 2>nul

set "OLD_HASH="
if exist "%REQ_HASH_FILE%" set /p OLD_HASH=<"%REQ_HASH_FILE%"

if /I "%ORCHARD_BRIDGE_REPAIR%"=="1" goto :install_deps
if not defined REQ_HASH goto :install_deps
if not "%REQ_HASH%"=="%OLD_HASH%" goto :install_deps
"%VENV_PY%" -c "import pymobiledevice3, PIL, pillow_heif, pystray, send2trash" >nul 2>nul
if errorlevel 1 goto :install_deps

echo [1/2] Dependencies already installed. Skipping pip install.
goto :start_gui

:install_deps
echo [1/2] Installing/updating packages inside .venv...
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [WARN] Could not upgrade pip tools. Continuing with the existing pip.
)
"%VENV_PY%" -m pip install --prefer-binary --no-warn-script-location -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install required packages inside .venv.
    echo Check your internet connection. If the folder is protected, right-click
    echo run_conda.bat and choose Run as administrator, then try again.
    pause
    exit /b 1
)
if defined REQ_HASH echo %REQ_HASH%>"%REQ_HASH_FILE%"

:start_gui
echo [2/2] Starting GUI...
"%VENV_PY%" main.py
set "EXITCODE=%ERRORLEVEL%"
echo.
echo Program exited with code %EXITCODE%.
pause
exit /b %EXITCODE%
