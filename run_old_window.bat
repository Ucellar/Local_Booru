@echo off
rem Local Booru 2 in the old window (the sidebar of Qt pages), whatever the setting "window" says:
rem for when the new window does not start on this computer.
cd /d "%~dp0"
set "LOCAL_BOORU_UI_SHELL=classic"
call run.bat
