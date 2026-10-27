@echo off
setlocal
cd /d "%~dp0"
echo =============================================
echo   DEVFORGE STUDIO - INICIAR SITE
 echo =============================================
if not exist ".venv\Scripts\python.exe" (
    echo Criando ambiente virtual...
    py -m venv .venv
    if errorlevel 1 goto erro
)
echo Instalando dependencias...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto erro
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo PRIMEIRO USO: arquivo .env criado!
    echo Configure SECRET_KEY, GOOGLE_MAIL_URL e DEVFORGE_MAIL_TOKEN no .env.
    echo Depois, execute: .venv\Scripts\python.exe app.py init-admin
    echo E execute este BAT novamente.
    pause
    exit /b 0
)
echo.
echo Abra no navegador: http://127.0.0.1:5000
echo Para criar admin: .venv\Scripts\python.exe app.py init-admin
echo.
".venv\Scripts\python.exe" app.py
pause
exit /b 0
:erro
echo Falha na instalacao. Verifique se o Python esta instalado.
pause
exit /b 1
