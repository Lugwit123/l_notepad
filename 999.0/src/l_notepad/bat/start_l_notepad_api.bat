@echo off
setlocal EnableDelayedExpansion

rem This bat lives at: <pkg>/src/l_notepad/bat
set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..\..\..") do set "PKG_ROOT=%%~fI"
set "SRC_DIR=%PKG_ROOT%\src"

set "L_NOTEPAD_ROOT=%PKG_ROOT%"
set "PYTHONPATH=%SRC_DIR%;%PYTHONPATH%"

if "%L_NOTEPAD_PORT%"=="" set "L_NOTEPAD_PORT=8765"
if "%L_NOTEPAD_HOST%"=="" set "L_NOTEPAD_HOST=127.0.0.1"

rem Keep a stable URL: if the target port is occupied, terminate the occupying process.
for /f %%P in ('powershell -NoProfile -Command "$p=%L_NOTEPAD_PORT%; Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique"') do (
  if not "%%P"=="0" (
    echo [l_notepad_api] Port %L_NOTEPAD_PORT% is occupied by PID %%P, terminating...
    taskkill /PID %%P /T /F >nul 2>&1
  )
)

echo [l_notepad_api] PKG_ROOT=%PKG_ROOT%
echo [l_notepad_api] http://%L_NOTEPAD_HOST%:%L_NOTEPAD_PORT%/
set "LOCAL_PY=%PKG_ROOT%\..\..\python\3.12\.python\python.exe"
if exist "%LOCAL_PY%" (
  set "PY_EXE=%LOCAL_PY%"
  echo [l_notepad_api] Using local python: !PY_EXE!
) else (
  set "PY_EXE=python"
  echo [l_notepad_api] Using python from PATH...
)
echo.

!PY_EXE! -m l_notepad.backend_server --host %L_NOTEPAD_HOST% --port %L_NOTEPAD_PORT%
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo.
  echo [l_notepad_api] ExitCode=%EC%
  pause
)
exit /b %EC%

