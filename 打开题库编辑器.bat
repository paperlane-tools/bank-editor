@echo off
rem ============================================================
rem  题库编辑器 —— 双击这个文件就能打开（也不用装任何东西）
rem  也可以把 .json 文件直接拖到本文件上，就会打开它
rem ============================================================
cd /d "%~dp0"
if exist "%~1" (set "YF=%~1") else (set "YF=")

where pythonw >nul 2>nul
if %errorlevel%==0 goto USE_W
goto USE_P

:USE_W
if defined YF (start "" pythonw "bank_editor.py" "%YF%") else (start "" pythonw "bank_editor.py")
goto :eof

:USE_P
if defined YF (python "bank_editor.py" "%YF%") else (python "bank_editor.py")
