@echo off
rem ---------------------------------------------------------------------------
rem  run_meshing.cmd - launch mesh_missile.py with the bundled PyFluent.
rem
rem  PyFluent (ansys-fluent-core) is installed in .\_deps and needs python 3.10
rem  or 3.11 (3.13 is not supported by this PyFluent release), so the launcher
rem  looks for a suitable interpreter before running the script.
rem
rem  This file must stay pure ASCII. cmd.exe parses a batch file with the active
rem  console code page, so non-ASCII comment text can swallow the following line:
rem  with UTF-8 Chinese comments the `set "HERE=..."` below was eaten, HERE
rem  stayed empty, ENTRY_SCRIPT degraded to a relative `mesh_missile.py`, and python
rem  then resolved it against the caller's cwd. The failure surfaced only as
rem  "can't open file '<workspace>\mesh_missile.py'".
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion
rem Inputs / outputs / parameters: paths are relative to this launcher;
rem %* forwards every run parameter unchanged. The entry script keeps its own
rem "Input / output / parameter configuration" block for the CLI defaults.
rem Interpreter: FLUENT_MESH_PYTHON first, then the local .python-path file,
rem then python 3.10 / 3.11 discovered through py -0p.
rem Required input: --geometry. Output directory and defaults live in mesh_runner.py.
set "HERE=%~dp0"
set "MESH_DEPS_DIR=%HERE%_deps"
set "PYTHON_CONFIG_FILE=%HERE%.python-path"
set "ENTRY_SCRIPT=%HERE%mesh_missile.py"
set "FALLBACK_PYTHON_310=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
set "FALLBACK_PYTHON_311=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
set "PYTHONPATH=%MESH_DEPS_DIR%"

if not exist "%ENTRY_SCRIPT%" (
  echo [run_meshing] entry script not found: "%ENTRY_SCRIPT%" 1>&2
  echo [run_meshing] launcher dir resolved to "%HERE%" 1>&2
  exit /b 3
)

set "PY="
if defined FLUENT_MESH_PYTHON if exist "%FLUENT_MESH_PYTHON%" set "PY=%FLUENT_MESH_PYTHON%"
if not defined PY if exist "%PYTHON_CONFIG_FILE%" set /p "PY="<"%PYTHON_CONFIG_FILE%"
if defined PY if not exist "%PY%" set "PY="
for %%P in (
  "%FALLBACK_PYTHON_310%"
  "%FALLBACK_PYTHON_311%"
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
"%PY%" "%ENTRY_SCRIPT%" %*
exit /b %ERRORLEVEL%
