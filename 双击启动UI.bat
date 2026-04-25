@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [DesicAi] 未检测到虚拟环境，正在运行安装脚本...
    python setup_environment.py
)

if not exist "src" (
    echo [DesicAi] 未检测到 src 目录，正在重新运行安装脚本...
    venv\Scripts\python.exe setup_environment.py
)

if not exist "venv\Scripts\python.exe" (
    echo [DesicAi] 虚拟环境仍未创建成功，请检查上方错误信息。
    pause
    exit /b 1
)

venv\Scripts\python.exe spa_server.py
pause
