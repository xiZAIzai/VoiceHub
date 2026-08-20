@echo off
rem Build VoiceHub daemon exe (one-dir): output dist\VoiceHub\VoiceHub.exe
rem Usage: run packaging\build_daemon.bat from repo root (ASCII-only for cmd compat)
setlocal
cd /d %~dp0..

if not exist .venv\Scripts\pyinstaller.exe (
  echo [ERROR] .venv\Scripts\pyinstaller.exe not found. Run: pip install -r requirements.txt
  exit /b 1
)

.venv\Scripts\pyinstaller.exe packaging\voicehub-daemon.spec --noconfirm --distpath dist --workpath build
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed
  exit /b 1
)

rem config.json ships next to the exe (user-editable), not bundled inside it
copy /y config.json dist\VoiceHub\config.json >nul

echo [OK] Build finished: dist\VoiceHub\VoiceHub.exe
endlocal
