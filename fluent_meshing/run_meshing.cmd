@echo off
rem ---------------------------------------------------------------------------
rem  run_meshing.cmd - launch mesh_missile.py with the bundled PyFluent.
rem
rem  PyFluent (ansys-fluent-core) is installed in .\_deps and needs python 3.10
rem  or 3.11 (3.13 is not supported by this PyFluent release), so the launcher
rem  looks for a suitable interpreter before running the script.
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%_deps"

set "PY="
if defined FLUENT_MESH_PYTHON if exist "%FLUENT_MESH_PYTHON%" set "PY=%FLUENT_MESH_PYTHON%"
if not defined PY if exist "%HERE%.python-path" set /p "PY="<"%HERE%.python-path"
if defined PY if not exist "%PY%" set "PY="
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) do (
  if not defined PY if exist %%P set "PY=%%~P"
)

if not defined PY (
  for /f "delims=" %%V in ('py -0p 2^>nul ^| findstr /r /c:"-V:3\.1[01]"') do (
    for %%W in (%%V) do (
      if not defined PY if exist "%%~W" set "PY=%%~W"
    )
  )
)

if not defined PY (
  echo [run_meshing] no python 3.10/3.11 interpreter found 1>&2
  exit /b 2
)

echo [run_meshing] using the configured Python interpreter and isolated _deps
"%PY%" "%HERE%mesh_missile.py" %*
exit /b %ERRORLEVEL%
