@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" ( echo Rode INICIAR_HUB.bat uma vez antes. & pause & exit /b 1 )
set "ATALHO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\STRAT-HUB.vbs"
> "%ATALHO%" echo CreateObject("WScript.Shell").Run "wscript.exe ""%CD%\tools\oculto.vbs""", 0, False
echo Pronto: o hub vai iniciar sozinho (sem janela) sempre que voce entrar no Windows.
echo Para desfazer: tools\REMOVER_INICIO_COM_WINDOWS.bat
pause
