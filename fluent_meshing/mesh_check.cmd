@echo off
rem ---------------------------------------------------------------------------
rem  mesh_check.cmd - verify an exported mesh with the inspection environment.
rem
rem  The exported file is checked by a separate interpreter whose h5py/numpy live
rem  in .mesh_inspect_deps_313, so the PyFluent environment in ._deps stays
rem  isolated. The interpreter is recorded in .mesh-inspect-python (git-ignored)
rem  or supplied through MESH_INSPECT_PYTHON.
rem
rem  This file must stay pure ASCII. cmd.exe parses a batch file with the active
rem  console code page, so non-ASCII comment text can swallow the following line
rem  and leave HERE empty; keep every comment in ASCII.
rem ---------------------------------------------------------------------------
setlocal
rem Inputs / outputs / parameters: paths are relative to this launcher;
rem %* forwards the positional mesh path unchanged. The interpreter comes from
rem MESH_INSPECT_PYTHON first, then the local config file; it stays isolated from
rem the PyFluent ._deps environment. The mesh file is a required positional
rem argument; this launcher only prints JSON and writes no report of its own.
set "HERE=%~dp0"
set "PYTHON_CONFIG_FILE=%HERE%.mesh-inspect-python"
set "INSPECT_DEPS_DIR=%HERE%.mesh_inspect_deps_313"
set "ENTRY_SCRIPT=%HERE%tools\inspect_mesh_file.py"
set "PY=%MESH_INSPECT_PYTHON%"

if not exist "%ENTRY_SCRIPT%" (
  echo [mesh_check] entry script not found: "%ENTRY_SCRIPT%" 1>&2
  echo [mesh_check] launcher dir resolved to "%HERE%" 1>&2
  exit /b 3
)

if not defined PY if exist "%PYTHON_CONFIG_FILE%" set /p "PY="<"%PYTHON_CONFIG_FILE%"
if defined PY if not exist "%PY%" set "PY="
if not defined PY (
  echo [mesh_check] no mesh-inspection interpreter configured; set MESH_INSPECT_PYTHON 1>&2
  exit /b 2
)
set "PYTHONPATH=%INSPECT_DEPS_DIR%"
"%PY%" "%ENTRY_SCRIPT%" %*
exit /b %ERRORLEVEL%
