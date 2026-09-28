@echo off
setlocal
title BI Rapor Agent
cd /d "%~dp0"

echo ============================================
echo   BI Rapor Agent baslatiliyor
echo ============================================

rem ---------- Ilk kurulum kontrolleri ----------
if not exist "backend\.venv\Scripts\python.exe" (
    echo [kurulum] Python sanal ortami olusturuluyor...
    python -m venv backend\.venv || goto :error
    backend\.venv\Scripts\python -m pip install -q --upgrade pip
    backend\.venv\Scripts\pip install -q -r backend\requirements.txt pyodbc || goto :error
)

if not exist "backend\.env" (
    echo [kurulum] backend\.env bulunamadi, .env.example kopyalaniyor. LLM ayarlarini duzenlemeyi unutmayin.
    copy /y "backend\.env.example" "backend\.env" >nul
)

if not exist "frontend\node_modules" (
    echo [kurulum] Frontend paketleri yukleniyor...
    pushd frontend
    call npm install || (popd & goto :error)
    popd
)

if not exist "frontend\dist-viewer\viewer.html" (
    echo [kurulum] HTML export sablonu derleniyor...
    pushd frontend
    call npm run build:viewer >nul || (popd & goto :error)
    popd
)

rem ---------- Backend ----------
curl -s -o nul http://localhost:8000/api/health
if %errorlevel%==0 (
    echo [backend] Zaten calisiyor: http://localhost:8000
) else (
    echo [backend] Baslatiliyor: http://localhost:8000
    start "BI Agent - Backend" /d "%~dp0backend" cmd /k ".venv\Scripts\python -X utf8 -m uvicorn app.main:app --port 8000"
)

rem ---------- Frontend ----------
curl -s -o nul http://localhost:5173
if %errorlevel%==0 (
    echo [frontend] Zaten calisiyor: http://localhost:5173
) else (
    echo [frontend] Baslatiliyor: http://localhost:5173
    start "BI Agent - Frontend" /d "%~dp0frontend" cmd /k "npm run dev"
)

rem ---------- Hazir olmasini bekle ----------
echo Sunucular bekleniyor...
set /a tries=0
:wait
set /a tries+=1
if %tries% gtr 60 goto :timeout
ping -n 2 127.0.0.1 >nul
curl -s -o nul http://localhost:8000/api/health || goto :wait
curl -s -o nul http://localhost:5173 || goto :wait

echo.
echo Hazir. Tarayici aciliyor: http://localhost:5173
echo Kapatmak icin "BI Agent - Backend" ve "BI Agent - Frontend" pencerelerini kapatin.
start "" http://localhost:5173
ping -n 4 127.0.0.1 >nul
exit /b 0

:timeout
echo.
echo [uyari] Sunucular 60 saniyede acilmadi. "BI Agent - Backend" / "Frontend" pencerelerindeki hatalara bakin.
pause
exit /b 1

:error
echo.
echo [hata] Kurulum basarisiz oldu. Yukaridaki mesajlara bakin.
pause
exit /b 1
