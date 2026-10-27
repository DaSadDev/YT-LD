@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo === [1/4] Cai dat package (yt-dlp[default] gom ca yt-dlp-ejs) ===
python -m pip install -U -r requirements.txt || goto :error

echo === [2/4] Chuan bi Deno (JS runtime de YouTube khong bi cham/403) ===
if exist deno.exe goto :have_deno
where deno >nul 2>nul && goto :have_deno
echo Dang tai deno.exe tu GitHub chinh thuc...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' -OutFile deno.zip; Expand-Archive -Path deno.zip -DestinationPath . -Force; Remove-Item deno.zip } catch { Write-Host 'Khong tai duoc deno:' $_.Exception.Message }"
if not exist deno.exe (
    echo [CANH BAO] Khong co Deno. Hay chay:  winget install DenoLand.Deno
    echo            hoac tai deno.exe dat canh file build_exe.bat roi build lai.
)
:have_deno

echo === [3/4] Dong goi thanh .exe ===
set EXTRA=
if exist ffmpeg.exe set EXTRA=%EXTRA% --add-binary "ffmpeg.exe;."
if exist ffprobe.exe set EXTRA=%EXTRA% --add-binary "ffprobe.exe;."
if exist ffmpeg.exe echo Tim thay ffmpeg.exe -^> se nhung vao file exe

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "YT Downloader" ^
    --collect-all yt_dlp ^
    --collect-all yt_dlp_ejs ^
    %EXTRA% ^
    yt_downloader.py || goto :error

echo === [4/4] Copy Deno canh file exe ===
if exist deno.exe copy /y deno.exe dist\ >nul

echo.
echo Xong! Thu muc dist\ gom:
echo    YT Downloader.exe
if exist dist\deno.exe echo    deno.exe   ^(PHAI de canh file exe, khong duoc xoa^)
pause
exit /b 0

:error
echo.
echo CO LOI xay ra. Kiem tra Python da them vao PATH chua (python --version).
pause
exit /b 1
