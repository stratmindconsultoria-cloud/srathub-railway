@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "TS=tailscale"
where tailscale >nul 2>nul || set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
if not exist "%TS%" if not "%TS%"=="tailscale" (
  echo   Tailscale nao encontrado. Instale em https://tailscale.com/download/windows
  echo   entre com sua conta e instale tambem o app Tailscale no celular, com a mesma conta.
  pause & exit /b 1
)
for /f "delims=" %%p in ('.venv\Scripts\python.exe tools\porta.py') do set "PORTA=%%p"
echo   Publicando o hub (porta %PORTA%) na sua rede privada Tailscale, com HTTPS...
"%TS%" serve --bg %PORTA%
echo.
"%TS%" serve status
echo.
echo   Use o endereco https://....ts.net mostrado acima no celular ou em outro computador
echo   (com o Tailscale ativo e a mesma conta). Se aparecer um link para habilitar HTTPS, abra-o e confirme.
pause
