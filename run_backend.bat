@echo off
setlocal EnableExtensions

title VideoMind AI - Backend (Streamlit)
cd /d "%~dp0"

echo ============================================================
echo  VideoMind AI - application server
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found: .venv\Scripts\python.exe
    echo.
    echo Create it and install the dependencies first:
    echo     py -3.12 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    if /I not "%~1"=="--nopause" pause
    exit /b 1
)

if not exist ".env" (
    echo [WARNING] .env was not found.
    echo The app still starts, but summaries and chat answers need NVIDIA_API_KEY.
    echo Copy .env.example to .env and add your key when you need them.
    echo.
)

if not defined STREAMLIT_PORT set "STREAMLIT_PORT=8501"

echo Starting VideoMind AI on http://localhost:%STREAMLIT_PORT%
echo.
echo One process serves both the backend logic and the web UI.
echo Open the frontend in your browser with:
echo     start "" "http://localhost:%STREAMLIT_PORT%"
echo.
echo Press Ctrl+C in this window to stop the server.
echo.

".venv\Scripts\python.exe" -m streamlit run app.py --server.port %STREAMLIT_PORT% --server.headless true --browser.gatherUsageStats false

set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Server stopped with exit code %EXIT_CODE%.
if /I not "%~1"=="--nopause" pause
exit /b %EXIT_CODE%
