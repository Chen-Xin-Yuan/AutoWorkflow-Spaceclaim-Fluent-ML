@echo off
rem ---------------------------------------------------------------------------
rem  mesh_check.cmd - verify an exported mesh with the inspection environment.
rem
rem  The exported file is checked by a separate interpreter whose h5py/numpy live
rem  in .mesh_inspect_deps_313, so the PyFluent environment in ._deps stays
rem  isolated. The interpreter is recorded in .mesh-inspect-python (git-ignored)
rem  or supplied through MESH_INSPECT_PYTHON.
rem ---------------------------------------------------------------------------
setlocal
set "HERE=%~dp0"
set "PY=%MESH_INSPECT_PYTHON%"
if not defined PY if exist "%HERE%.mesh-inspect-python" set /p "PY="<"%HERE%.mesh-inspect-python"
if defined PY if not exist "%PY%" set "PY="
if not defined PY (
  echo [mesh_check] no mesh-inspection interpreter configured; set MESH_INSPECT_PYTHON 1>&2
  exit /b 2
)
set "PYTHONPATH=%HERE%.mesh_inspect_deps_313"
"%PY%" "%HERE%tools\inspect_mesh_file.py" %*
exit /b %ERRORLEVEL%
