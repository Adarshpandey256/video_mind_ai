@echo off
setlocal EnableExtensions

title VideoMind AI - Frontend (browser)

if not defined STREAMLIT_PORT set "STREAMLIT_PORT=8501"

echo Opening the VideoMind AI frontend at http://localhost:%STREAMLIT_PORT%
echo.
echo The UI is served by run_backend.bat - start that first if the page does not load.
echo.

start "" "http://localhost:%STREAMLIT_PORT%"
