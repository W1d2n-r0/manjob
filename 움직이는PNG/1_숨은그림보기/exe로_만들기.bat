@echo off
chcp 65001 >nul
python -m pip install --quiet PySide6 numpy pyinstaller
pyinstaller --noconfirm --onefile --windowed --name soomgeurim --paths "%~dp0" "%~dp0soomgeurim.py"
echo.
echo dist 폴더에 soomgeurim.exe 가 생겼습니다.
pause
