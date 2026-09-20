@echo off
chcp 65001 >nul
setlocal EnableExtensions
pushd "%~dp0"
title ttcut

rem ================================================================
rem  ttcut 啟動器（Windows）／ ttcut launcher for Windows
rem  跟 ttcut_v*.py、ffmpeg.exe、ffprobe.exe 放在同一個資料夾，點兩下即可。
rem
rem  ttcut.bat 和 ttcut_EN.bat 內容完全相同，差別只在檔名：
rem  檔名以 _EN 結尾就優先開英文版、顯示英文訊息，其他檔名是中文。
rem  資料夾裡有好幾個版本時，依檔名裡的版本號開最新的一版。
rem
rem  存檔必須是 UTF-8（不含 BOM）加 CRLF 換行：
rem  有 BOM 第一行會壞掉；只有 LF 的話 goto 可能找不到標籤。
rem  訊息文字只用全形標點，不要用半形括號和其他指令符號：
rem  在 if 區塊裡，半形右括號會提早結束區塊，其他符號會被當成指令。
rem ================================================================

rem ---- 0. 語言：看這個 .bat 自己的檔名，裡面有 _EN 就是英文
rem  用「包含」而不是「結尾是」：瀏覽器重複下載會存成 ttcut_EN (1).bat
set "TTLANG=zh"
set "SELF=%~n0"
if /i not "%SELF:_EN=%"=="%SELF%" set "TTLANG=en"
rem  以 ZH 或 EN 變數開頭的行：目前語言的那行變成 echo，另一行變成 rem
set "ZH=echo"
set "EN=rem"
if "%TTLANG%"=="en" (
    set "ZH=rem"
    set "EN=echo"
)

rem ---- 1. 資料夾裡要有 ttcut 的 .py
if not exist "ttcut*.py" (
    echo.
    %ZH%  找不到 ttcut 的 .py 檔。
    %ZH%  請把這個 .bat 跟 ttcut 的 .py 放在同一個資料夾，例如 ttcut_v2_5.py。
    %ZH%  如果是在 zip 裡直接點兩下，請先把整個 zip 解壓縮再執行。
    %EN%  No ttcut .py file in this folder.
    %EN%  Put this .bat in the same folder as a ttcut .py file, for example ttcut_v2_5_EN.py.
    %EN%  If you double-clicked it inside a zip, extract the whole zip first.
    goto :fail
)

rem ---- 2. 找 Python
rem  py：python.org 的安裝程式和 Python 安裝管理員都會裝。這裡只用 where 找，先不執行，
rem  因為安裝管理員第一次執行會跑設定檢查、可能問問題或自動下載，那些畫面不能藏起來。
rem  python：備案。沒裝 Python 時 Windows 內建的 python 只是開商店的捷徑，所以要真的跑跑看。
rem  用 if errorlevel 判斷，不用「把錯誤導向 1 號再接條件執行」的寫法：
rem  兩者意思一樣，但有些 cmd 實作會把後者解析錯，結果不管成敗都往下執行
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py"
if not defined PY (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo.
    %ZH%  這台電腦還沒有安裝 Python。
    %ZH%  接下來會打開 Python 官網：下載、用預設選項安裝，裝好再點一次這個 .bat。
    %ZH%  如果剛剛已經裝好了，請重新開機後再試。
    %EN%  Python is not installed on this computer.
    %EN%  The Python website will open now. Download it, install with the default options,
    %EN%  then double-click this .bat again.
    %EN%  If you have just installed it, restart the computer and try again.
    start "" "https://www.python.org/downloads/"
    goto :fail
)

rem  這一行刻意不隱藏輸出：第一次執行若出現設定問題或下載進度，使用者才看得到
%PY% -c "import sys; sys.exit(3 if sys.version_info < (3, 8) else 0)"
set "RC=%errorlevel%"
if "%RC%"=="3" (
    echo.
    %ZH%  Python 版本太舊，ttcut 需要 3.8 以上，請到 python.org 安裝新版。
    %EN%  This Python is too old. ttcut needs 3.8 or newer, available from python.org.
    goto :fail
)
if not "%RC%"=="0" (
    echo.
    %ZH%  Python 無法啟動，原因請看上面的訊息。
    %ZH%  必要時到 python.org 重新安裝 Python。
    %EN%  Python could not start. The reason is in the messages above.
    %EN%  If needed, reinstall Python from python.org.
    goto :fail
)

%PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    %ZH%  這份 Python 缺少 tkinter，按「載入影片」不會跳出選檔視窗。
    %ZH%  請到 python.org 重新安裝完整版的 Python。
    %EN%  This Python is missing tkinter, so Load video cannot open the file picker.
    %EN%  Reinstall the full Python from python.org.
    goto :fail
)

rem ---- 3. 挑要開的 .py：語言相符的優先，其次版本號最大
rem  不看修改日期：從 GitHub 下載的 zip 解開後，每個檔案的日期都一樣。
rem  版本號要當數字比：v2_10 比 v2_9 新，照字母排會排錯，所以交給 Python 挑。
set "SCRIPT="
for /f "usebackq delims=" %%F in (`%PY% -c "import glob,re,sys; en=sys.argv[1]=='en'; v=[(bool(m.group(3))==en, int(m.group(1)), int(m.group(2)), f) for f in glob.glob('ttcut*.py') for m in [re.fullmatch(r'ttcut_v(\d+)_(\d+)(_en)?(?: \(\d+\))?\.py', f, re.I)] if m]; print(max(v)[3] if v else (sorted(glob.glob('ttcut*.py')) + [''])[0])" %TTLANG% 2^>nul`) do set "SCRIPT=%%F"
if not defined SCRIPT for /f "delims=" %%F in ('dir /b /a-d "ttcut*.py" 2^>nul') do if not defined SCRIPT set "SCRIPT=%%F"

rem ---- 4. ffmpeg 與 ffprobe：放在這個資料夾，或已經裝進 PATH
set "NO_FF="
set "NO_PROBE="
if not exist "ffmpeg.exe" (
    where ffmpeg >nul 2>nul
    if errorlevel 1 set "NO_FF=1"
)
if not exist "ffprobe.exe" (
    where ffprobe >nul 2>nul
    if errorlevel 1 set "NO_PROBE=1"
)
if defined NO_FF (
    echo.
    %ZH%  注意：找不到 ffmpeg.exe。可以標記、匯出 JSON，但無法製作成片。
    %ZH%        請把 ffmpeg.exe 和 ffprobe.exe 一起放進這個資料夾。
    %EN%  Note: ffmpeg.exe not found. You can tag and export JSON, but not render.
    %EN%        Put ffmpeg.exe and ffprobe.exe in this folder.
) else if defined NO_PROBE (
    echo.
    %ZH%  注意：有 ffmpeg.exe，但少了 ffprobe.exe。
    %ZH%        成片做得出來，但讀不到影片規格：60fps 的影片可能輸出成 30fps，
    %ZH%        iPhone 的 HDR 也不會轉換，顏色會發灰。
    %ZH%        ffprobe.exe 就在同一個 ffmpeg 壓縮檔的 bin 資料夾裡。
    %EN%  Note: ffmpeg.exe is here but ffprobe.exe is missing.
    %EN%        Rendering still works, but the video's specs cannot be read: 60 fps footage
    %EN%        may come out at 30 fps, and iPhone HDR is not converted, so colours look grey.
    %EN%        ffprobe.exe is in the same bin folder of the ffmpeg zip.
)

rem ---- 5. 啟動
rem  ttcut 第一行是給 Mac 用的 #!/usr/bin/env python3；叫舊版 py 不要照它去 PATH 找 python3，
rem  免得找到 Windows 內建那個會開商店的 python3 捷徑
set "PYLAUNCHER_NO_SEARCH_PATH=1"
echo.
%ZH%  正在啟動 %SCRIPT%，瀏覽器會自動打開。
%ZH%  使用期間請讓這個視窗開著；用完直接關掉這個視窗就會結束。
%EN%  Starting %SCRIPT%. Your browser will open by itself.
%EN%  Keep this window open while you work. Closing it shuts ttcut down.
echo.
%PY% "%SCRIPT%"
if errorlevel 1 goto :fail
popd
exit /b 0

:fail
echo.
%ZH%  按任意鍵關閉這個視窗。
%EN%  Press any key to close this window.
pause >nul
popd
exit /b 1
