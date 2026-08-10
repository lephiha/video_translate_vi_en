@echo off
REM build.bat — build exe (PyInstaller) roi build installer (Inno Setup).
REM Chay tren Windows, trong venv da cai requirements.txt + pyinstaller.
REM Can cai san Inno Setup 6 (https://jrsoftware.org/isdl.php) va co iscc.exe trong PATH.

setlocal

echo === [1/2] Building exe voi PyInstaller ===
pyinstaller app.spec --noconfirm
if errorlevel 1 (
    echo Build PyInstaller that bai.
    exit /b 1
)

if not exist "dist\VideoDubEnVi\VideoDubEnVi.exe" (
    echo Khong tim thay dist\VideoDubEnVi\VideoDubEnVi.exe - build that bai.
    exit /b 1
)

dir /b /s "dist\VideoDubEnVi\ffmpeg*.exe" >nul 2>nul
if errorlevel 1 (
    echo Khong tim thay FFmpeg trong dist - dung build de tranh tao installer bi mat audio.
    exit /b 1
)

echo === [2/2] Building installer voi Inno Setup ===
where iscc >nul 2>nul
if errorlevel 1 (
    echo Khong tim thay iscc.exe trong PATH. Cai Inno Setup roi them thu muc cai dat vao PATH.
    exit /b 1
)

iscc installer.iss
if errorlevel 1 (
    echo Build Inno Setup that bai.
    exit /b 1
)

echo.
echo === XONG ===
echo Installer nam tai: installer_output\VideoDubEnVi-Setup-*.exe

endlocal
