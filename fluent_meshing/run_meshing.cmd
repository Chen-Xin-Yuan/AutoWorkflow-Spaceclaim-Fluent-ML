@echo off
rem ---------------------------------------------------------------------------
rem  run_meshing.cmd - launch mesh_missile.py with the bundled PyFluent.
rem
rem  PyFluent (ansys-fluent-core) is installed in .\_deps and needs python 3.10
rem  or 3.11 (3.13 is not supported by this PyFluent release), so the launcher
rem  looks for a suitable interpreter before running the script.
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion
rem 输入 / 输出 / 参数配置：路径相对本启动器；%* 原样转发全部运行参数。
rem 解释器优先 FLUENT_MESH_PYTHON，其次本地配置，再按既有顺序查找 3.10/3.11。
rem 输入模型必填 --geometry；运行输出及 CLI 默认值见 mesh_runner.py 文件前部。
set "HERE=%~dp0"
set "MESH_DEPS_DIR=%HERE%_deps"
set "PYTHON_CONFIG_FILE=%HERE%.python-path"
set "ENTRY_SCRIPT=%HERE%mesh_missile.py"
set "FALLBACK_PYTHON_310=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
set "FALLBACK_PYTHON_311=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
set "PYTHONPATH=%MESH_DEPS_DIR%"

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
