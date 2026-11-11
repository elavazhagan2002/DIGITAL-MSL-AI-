@echo off
setlocal EnableExtensions

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend"
set "FRONTEND_DIR=%ROOT_DIR%frontend"

if not exist "%BACKEND_DIR%\venv\Scripts\python.exe" (
  echo Backend virtual environment not found at %BACKEND_DIR%\venv.
  echo Create it with: cd backend ^&^& python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)

if not exist "%FRONTEND_DIR%\node_modules" (
  echo Installing frontend dependencies...
  pushd "%FRONTEND_DIR%"
  call npm install
  if errorlevel 1 (
    popd
    echo npm install failed.
    pause
    exit /b 1
  )
  popd
)

echo Starting Digital MSL backend on http://127.0.0.1:8087
start "Digital MSL backend" /D "%BACKEND_DIR%" cmd /k ""%BACKEND_DIR%\venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8087 --reload"

echo Starting Digital MSL frontend on http://localhost:5173
start "Digital MSL frontend" /D "%FRONTEND_DIR%" cmd /k "npm run dev"

timeout /t 4 /nobreak >nul
start "" "http://localhost:5173"
echo Both servers are starting. Keep the two new windows open while you use the app.
endlocal
