@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================
echo  아스키 아트 메이커 - 실행파일(.exe) 만들기
echo  ASCII Art Maker - build .exe
echo ============================================
echo.
echo [1/2] 필요한 패키지 설치 중... (Installing packages)
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail
echo.
echo [2/2] 실행파일 만드는 중... 2~5분 걸려요 (Building, 2-5 min)
python -m PyInstaller --noconfirm --onefile --windowed --name AsciiArtMaker --hidden-import PIL._tkinter_finder --exclude-module mediapipe --exclude-module matplotlib --exclude-module scipy --exclude-module pandas askill_art_maker.py
if errorlevel 1 goto fail
copy /y "dist\AsciiArtMaker.exe" "AsciiArtMaker.exe" > nul
echo.
echo 완료! 이 폴더의 AsciiArtMaker.exe 를 실행하세요. (Done: AsciiArtMaker.exe)
pause
exit /b 0
:fail
echo.
echo 실패했습니다. 위 메시지를 확인해주세요. (Build failed - see messages above)
pause
exit /b 1
