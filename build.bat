@echo off
rem Builds dist\GameSpecCheck.exe, a single file that runs without Python.
rem PyInstaller is installed into a local .venv folder, not system-wide.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv || python -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip pyinstaller || goto :fail
".venv\Scripts\python.exe" tools\make_icon.py build\icon.ico || goto :fail
".venv\Scripts\python.exe" tools\make_version_info.py build\version_info.txt || goto :fail
".venv\Scripts\python.exe" -m unittest discover tests || goto :fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile ^
  --name GameSpecCheck --icon "%~dp0build\icon.ico" ^
  --version-file "%~dp0build\version_info.txt" ^
  --add-data "%~dp0web;web" ^
  --add-data "%~dp0LICENSE;legal" ^
  --add-data "%~dp0NOTICE;legal" ^
  --add-data "%~dp0CREDITS.md;legal" ^
  --add-data "%~dp0licenses\PSF-LICENSE.txt;legal" ^
  --workpath build\pyinstaller --specpath build ^
  run.py || goto :fail

rem Signs the exe when a certificate is configured (see tools\sign.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\sign.ps1" "%~dp0dist\GameSpecCheck.exe" || goto :fail

echo.
echo Built dist\GameSpecCheck.exe
exit /b 0

:fail
echo.
echo The build failed. See the messages above.
exit /b 1
