@echo off
rem Build VoiceHub daemon exe (one-dir): output dist\VoiceHub\VoiceHub.exe
rem Works locally (repo .venv) and on CI (system python). ASCII-only for cmd compat.
setlocal
cd /d %~dp0..

rem Pick python: repo venv if present, else system python (CI)
set PY=python
if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python.exe

%PY% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] PyInstaller not found. Run: pip install -r requirements.txt
  exit /b 1
)

%PY% -m PyInstaller packaging\voicehub-daemon.spec --noconfirm --distpath dist --workpath build
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed
  exit /b 1
)

rem config.json ships next to the exe (user-editable), not bundled inside it
copy /y config.json dist\VoiceHub\config.json >nul

echo [OK] Build finished: dist\VoiceHub\VoiceHub.exe
endlocal
