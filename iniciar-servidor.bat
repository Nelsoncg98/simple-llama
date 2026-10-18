@echo off
title Simple Llama (llama.cpp)
cd /d "%~dp0"
python proxy.py %*
pause
