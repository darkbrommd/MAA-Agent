@echo off
chcp 65001 >nul
cd /d "%~dp0"
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
