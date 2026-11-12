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
".venv\Scripts\python.exe" -m unittest discover tests || goto :fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile ^
  --name GameSpecCheck --icon "%~dp0build\icon.ico" ^
  --add-data "%~dp0web;web" ^
  --workpath build\pyinstaller --specpath build ^
  run.py || goto :fail

echo.
echo Built dist\GameSpecCheck.exe
exit /b 0

:fail
echo.
echo The build failed. See the messages above.
exit /b 1
