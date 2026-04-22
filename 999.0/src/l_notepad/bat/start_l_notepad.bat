@echo off
setlocal EnableDelayedExpansion

rem This bat lives at: <pkg>/src/l_notepad/bat
set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..\..\..") do set "PKG_ROOT=%%~fI"
set "SRC_DIR=%PKG_ROOT%\src"

set "L_NOTEPAD_ROOT=%PKG_ROOT%"
set "PYTHONPATH=%SRC_DIR%;%PYTHONPATH%"

echo [l_notepad] PKG_ROOT=%PKG_ROOT%
set "LOCAL_PY=%PKG_ROOT%\..\..\python\3.12\.python\python.exe"
if exist "%LOCAL_PY%" (
  set "PY_EXE=%LOCAL_PY%"
  echo [l_notepad] Using local python: !PY_EXE!
) else (
  set "PY_EXE=python"
  echo [l_notepad] Using python from PATH...
)
echo.

!PY_EXE! -m l_notepad.main
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo.
  echo [l_notepad] ExitCode=%EC%
  pause
)
exit /b %EC%

