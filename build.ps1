# Builds dist\battery-tray.exe: a single file, no console window.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { py -m venv .venv }
& $python -m pip install --quiet -e . pyinstaller
if ($LASTEXITCODE) { exit $LASTEXITCODE }

New-Item -ItemType Directory -Force build | Out-Null
& $python -c "from battery_tray import icon; icon.app_icon('#FFFFFF', 256, back='#2B2B2B').save('build/app.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)])"
if ($LASTEXITCODE) { exit $LASTEXITCODE }

& $python -m PyInstaller --noconfirm --clean --onefile --noconsole --name battery-tray `
    --icon "$PSScriptRoot\build\app.ico" --specpath build --hidden-import pystray._win32 battery_tray\__main__.py
exit $LASTEXITCODE
