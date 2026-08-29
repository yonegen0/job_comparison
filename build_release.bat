@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

set "BUILD_PY=py -3.13"
%BUILD_PY% -c "import platform,sys; raise SystemExit(0 if sys.version_info[:3]==(3,13,13) and platform.architecture()[0]=='64bit' else 1)"
if errorlevel 1 (
  echo [ERROR] CPython 3.13.13 x64 is required.
  exit /b 1
)

if not exist ".venv-build\Scripts\python.exe" %BUILD_PY% -m venv ".venv-build"
if errorlevel 1 exit /b 1

if not exist ".venv-build\tmp" mkdir ".venv-build\tmp"
if errorlevel 1 exit /b 1
set "TEMP=%CD%\.venv-build\tmp"
set "TMP=%TEMP%"
set "PYTHONPYCACHEPREFIX=%TEMP%\pycache"

".venv-build\Scripts\python.exe" -m pip install -r requirements-build-win11.lock -r requirements-win11.lock
if errorlevel 1 exit /b 1
".venv-build\Scripts\python.exe" -m pip install --no-deps --no-build-isolation .
if errorlevel 1 exit /b 1

set "APP_VERSION="
set "VERSION_FILE=%TEMP%\job-comparison-tool-version.txt"
".venv-build\Scripts\python.exe" -c "import job_comparison_app; print(job_comparison_app.__version__)" > "%VERSION_FILE%"
if errorlevel 1 exit /b 1
set /p "APP_VERSION="<"%VERSION_FILE%"
del /q "%VERSION_FILE%" >nul 2>&1
if not defined APP_VERSION (
  echo [ERROR] Application version could not be determined.
  exit /b 1
)

".venv-build\Scripts\python.exe" -m pytest tests -q -p no:cacheprovider --basetemp ".venv-build\pytest-temp"
if errorlevel 1 exit /b 1
".venv-build\Scripts\python.exe" -m compileall -q job_comparison_app job_pdf_tool job_comparison_report tests
if errorlevel 1 exit /b 1
".venv-build\Scripts\python.exe" -m PyInstaller --clean --noconfirm JobComparisonTool.spec
if errorlevel 1 exit /b 1

set "EXPECTED_ZIP=release\JobComparisonTool-%APP_VERSION%-win-x64.zip"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\release_checks.ps1" -ReleaseDirectory "dist\JobComparisonTool" -ExpectedVersion "%APP_VERSION%" -ExpectedZip "%EXPECTED_ZIP%"
if errorlevel 1 exit /b 1

echo Release %APP_VERSION% created under release\current and release\
exit /b 0
