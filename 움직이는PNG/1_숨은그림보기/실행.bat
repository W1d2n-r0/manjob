@echo off
chcp 65001 >nul
python -m pip install --quiet PySide6 numpy
start "" pythonw "%~dp0soomgeurim.py" %*
