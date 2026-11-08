@echo off
chcp 65001 >nul
title STRAT-HUB
cd /d "%~dp0"
echo.
echo   STRAT-HUB - iniciando...
echo.
if exist ".venv\Scripts\python.exe" goto preparar
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo   [ERRO] Python nao encontrado. Instale o Python 3.11+ em https://www.python.org/downloads/
  echo          e marque a opcao "Add python.exe to PATH" na instalacao.
  pause & exit /b 1
)
echo   Criando ambiente Python do hub (.venv)...
%PY% -m venv .venv || ( echo   [ERRO] falha ao criar .venv & pause & exit /b 1 )
:preparar
for /f "delims=" %%p in ('.venv\Scripts\python.exe tools\preparar.py') do set "PORTA=%%p"
if not defined PORTA ( echo   [ERRO] falha ao instalar dependencias & pause & exit /b 1 )
echo   Portal: http://localhost:%PORTA%
echo   (feche esta janela ou use PARAR_HUB.bat para desligar)
echo.
".venv\Scripts\python.exe" -m hub --abrir
pause
