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
rem 输入 / 输出 / 参数配置：路径相对本启动器；%* 原样转发网格位置参数。
rem 解释器优先 MESH_INSPECT_PYTHON，其次本地配置；与 PyFluent 的 _deps 隔离。
rem 网格文件为必填位置参数；只打印 JSON，启动器不写输出报告。
set "HERE=%~dp0"
set "PYTHON_CONFIG_FILE=%HERE%.mesh-inspect-python"
set "INSPECT_DEPS_DIR=%HERE%.mesh_inspect_deps_313"
set "ENTRY_SCRIPT=%HERE%tools\inspect_mesh_file.py"
set "PY=%MESH_INSPECT_PYTHON%"
if not defined PY if exist "%PYTHON_CONFIG_FILE%" set /p "PY="<"%PYTHON_CONFIG_FILE%"
if defined PY if not exist "%PY%" set "PY="
if not defined PY (
  echo [mesh_check] no mesh-inspection interpreter configured; set MESH_INSPECT_PYTHON 1>&2
  exit /b 2
)
set "PYTHONPATH=%INSPECT_DEPS_DIR%"
"%PY%" "%ENTRY_SCRIPT%" %*
exit /b %ERRORLEVEL%
