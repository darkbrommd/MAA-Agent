@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m unittest discover -s tests -v
) else (
  python -m unittest discover -s tests -v
)
echo.
if errorlevel 1 (
  echo [FAIL] Regression tests failed. Do not restart the production Agent yet.
) else (
  echo [OK] All regression tests passed.
)
pause
