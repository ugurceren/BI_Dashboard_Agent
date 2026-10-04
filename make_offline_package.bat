@echo off
rem Internet olan bir bilgisayarda calistirin. Varsayilan: GUNCELLEME paketi (kod + arayuz, ~5 MB).
rem Ilk kurulum / yeni bilgisayar icin: make_offline_package.bat --full  (Python paketleri dahil, ~55 MB)
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
    echo Once start.bat ile kurulumu tamamlayin ^(bu bilgisayarda internet gerekli^).
    pause
    exit /b 1
)
backend\.venv\Scripts\python -X utf8 backend\scripts\make_offline_package.py %*
pause
