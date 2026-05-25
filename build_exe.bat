@echo off
chcp 65001 >nul
echo ========================================
echo   Disk Analyzer - Windows 打包脚本
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.7+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] 安装 PyInstaller...
pip install pyinstaller

echo.
echo [2/3] 开始打包（含图标）...
pyinstaller --onefile --windowed --name DiskAnalyzer ^
    --icon=app.ico ^
    --add-data "app.ico;." ^
    --add-data "README.md;." ^
    disk_analyzer.py

echo.
echo [3/3] 打包完成！
echo.
echo 输出文件: dist\DiskAnalyzer.exe
echo.
echo 可以拷贝 dist\DiskAnalyzer.exe 到任意 Windows 电脑直接运行。
pause
