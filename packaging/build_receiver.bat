@echo off
rem Build VoiceHub receiver exe (one-dir): output dist\VoiceHubReceiver\VoiceHubReceiver.exe
rem Usage: run packaging\build_receiver.bat from repo root (ASCII-only for cmd compat)
setlocal
cd /d %~dp0..

if not exist .venv\Scripts\pyinstaller.exe (
  echo [ERROR] .venv\Scripts\pyinstaller.exe not found. Run: pip install -r requirements.txt
  exit /b 1
)

.venv\Scripts\pyinstaller.exe packaging\voicehub-receiver.spec --noconfirm --distpath dist --workpath build
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed
  exit /b 1
)

echo [OK] Build finished: dist\VoiceHubReceiver\VoiceHubReceiver.exe
echo Default name is "laptop" (must match daemon config target key)
endlocal
