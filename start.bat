@echo off
setlocal EnableDelayedExpansion
title BI Lens
cd /d "%~dp0"

echo ============================================
echo   BI Lens baslatiliyor
echo ============================================

rem ---------- Python (en az 3.10) ----------
set "VPY=backend\.venv\Scripts\python.exe"
if exist "%VPY%" (
    "%VPY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [hata] backend\.venv eski bir Python ile olusturulmus ^(en az 3.10 gerekli^).
        echo        backend\.venv klasorunu silip Python 3.11 / 3.12 kurduktan sonra start.bat'i yeniden calistirin.
        goto :error
    )
) else (
    set "PYEXE="
    for %%c in ("py -3.12" "py -3.11" "py -3.10" "py -3" "python") do (
        if not defined PYEXE (
            %%~c -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
            if not errorlevel 1 set "PYEXE=%%~c"
        )
    )
    if not defined PYEXE (
        echo [hata] Python 3.10 veya ustu bulunamadi.
        echo        https://www.python.org/downloads/ adresinden Python 3.11 / 3.12 ^(64-bit^) kurun;
        echo        kurulumda "Add python.exe to PATH" secenegini isaretleyin.
        echo        ^(Windows "python" yazinca Microsoft Store aciyorsa: Ayarlar ^> Uygulamalar ^> Uygulama yurutme diger adlari ^> python kapatin^)
        goto :error
    )
    echo [kurulum] Python sanal ortami olusturuluyor ^(!PYEXE!^)...
    !PYEXE! -m venv backend\.venv || goto :error
    "%VPY%" -m pip install -q --upgrade pip
)

rem Python paketleri: requirements.txt degistiyse (ilk kurulum ya da yeni surum) guncellenir
fc /b "backend\requirements.txt" "backend\.venv\requirements.stamp" >nul 2>&1
if errorlevel 1 (
    echo [kurulum] Python paketleri yukleniyor / guncelleniyor...
    "%VPY%" -m pip install -q -r backend\requirements.txt || (
        echo [hata] Python paketleri yuklenemedi. Internet / kurumsal proxy gerekebilir:
        echo        set HTTPS_PROXY=http://proxy.kurum:8080   ya da pip icin ic paket aynasi ^(--index-url^)
        goto :error
    )
    copy /y "backend\requirements.txt" "backend\.venv\requirements.stamp" >nul
)

if not exist "backend\.env" (
    echo [kurulum] backend\.env bulunamadi, .env.example kopyalaniyor. LLM ayarlarini duzenlemeyi unutmayin.
    copy /y "backend\.env.example" "backend\.env" >nul
)

rem Frontend paketleri: package-lock.json degistiyse (ilk kurulum ya da yeni surum) guncellenir
where npm >nul 2>&1 || (
    echo [hata] Node.js / npm bulunamadi. https://nodejs.org adresinden Node.js 20+ LTS kurun.
    goto :error
)
fc /b "frontend\package-lock.json" "frontend\node_modules\.bi-lens.stamp" >nul 2>&1
if errorlevel 1 (
    echo [kurulum] Frontend paketleri yukleniyor / guncelleniyor...
    pushd frontend
    call npm install || (popd & goto :error)
    popd
    copy /y "frontend\package-lock.json" "frontend\node_modules\.bi-lens.stamp" >nul
    if exist "frontend\node_modules\.vite" rmdir /s /q "frontend\node_modules\.vite"
)

rem HTML export sablonu: yoksa ya da kod (git surumu) degistiyse yeniden derlenir
set "BI_REV="
for /f %%r in ('git -C "%~dp0." rev-parse HEAD 2^>nul') do set "BI_REV=%%r"
set "BI_OLD="
if exist "frontend\dist-viewer\.rev" set /p BI_OLD=<"frontend\dist-viewer\.rev"
set "BI_BUILD="
if not exist "frontend\dist-viewer\viewer.html" set "BI_BUILD=1"
if defined BI_REV if not "%BI_REV%"=="%BI_OLD%" set "BI_BUILD=1"
if defined BI_BUILD (
    echo [kurulum] HTML export sablonu derleniyor...
    pushd frontend
    call npm run build:viewer >nul || (popd & goto :error)
    popd
    if defined BI_REV (echo %BI_REV%)>"frontend\dist-viewer\.rev"
)

rem ---------- Backend portu ----------
rem Onceki calistirmanin portu backend\.port'ta; o portta backend calisiyorsa yeniden baslatilmaz.
set "BI_PORT="
if exist "backend\.port" set /p BI_PORT=<"backend\.port"
set "BACKEND_UP="
if defined BI_PORT (
    curl -s -f -o nul --max-time 20 http://127.0.0.1:!BI_PORT!/api/health && set "BACKEND_UP=1"
)
if not defined BACKEND_UP (
    rem 8000 dolu ya da Windows tarafindan ayrilmissa (Hyper-V / WSL / Docker) ilk bos port secilir
    for /f %%p in ('"%VPY%" backend\scripts\free_port.py 8000') do set "BI_PORT=%%p"
    if not "!BI_PORT!"=="8000" echo [bilgi] 8000 portu kullanilamiyor; backend !BI_PORT! portunda acilacak.
    (echo !BI_PORT!)>"backend\.port"

    rem acilis oncesi hizli kontrol: Python / paketler / .env / port / uygulama kodu
    echo [kontrol] Ortam kontrol ediliyor...
    "%VPY%" -X utf8 backend\scripts\doctor.py --quick --port !BI_PORT! > "%TEMP%\bi-lens-doctor.txt" 2>&1
    if errorlevel 1 (
        type "%TEMP%\bi-lens-doctor.txt"
        echo.
        echo [hata] Backend acilamaz; yukaridaki HATA satirlarina bakin.
        goto :error
    )
    echo [backend] Baslatiliyor: http://127.0.0.1:!BI_PORT!
    start "BI Lens - Backend" /d "%~dp0backend" cmd /k ".venv\Scripts\python -X utf8 -m uvicorn app.main:app --host 127.0.0.1 --port !BI_PORT!"
) else (
    echo [backend] Zaten calisiyor: http://127.0.0.1:!BI_PORT!
)

rem ---------- Frontend ----------
curl -s -o nul --max-time 5 http://localhost:5173
if %errorlevel%==0 (
    echo [frontend] Zaten calisiyor: http://localhost:5173
    if not "!BI_PORT!"=="8000" echo [uyari] Frontend daha once acilmis; backend portu degistiyse "BI Lens - Frontend" penceresini kapatip start.bat'i yeniden calistirin.
) else (
    echo [frontend] Baslatiliyor: http://localhost:5173
    start "BI Lens - Frontend" /d "%~dp0frontend" cmd /k "set BI_BACKEND_PORT=!BI_PORT!&& npm run dev"
)

rem ---------- Hazir olmasini bekle (ilk acilista sozluk yuklemesi uzun surebilir) ----------
echo Sunucular bekleniyor (en fazla 3 dakika)...
set /a tries=0
:wait
set /a tries+=1
if %tries% gtr 90 goto :timeout
ping -n 3 127.0.0.1 >nul
curl -s -f -o nul --max-time 30 http://127.0.0.1:!BI_PORT!/api/health || goto :wait
curl -s -o nul --max-time 5 http://localhost:5173 || goto :wait

echo.
echo Hazir. Tarayici aciliyor: http://localhost:5173
echo Kapatmak icin "BI Lens - Backend" ve "BI Lens - Frontend" pencerelerini kapatin.
start "" http://localhost:5173
ping -n 4 127.0.0.1 >nul
exit /b 0

:timeout
echo.
echo [uyari] Sunucular 3 dakikada hazir olmadi. Ortam kontrolu calistiriliyor...
echo.
"%VPY%" -X utf8 backend\scripts\doctor.py --skip-port
echo.
echo "BI Lens - Backend" penceresindeki son hata mesajina da bakin. Bu kontrolu elle calistirmak icin: check.bat
pause
exit /b 1

:error
echo.
echo [hata] Baslatma basarisiz oldu. Yukaridaki mesajlara bakin. Ayrintili kontrol icin: check.bat
pause
exit /b 1
