@echo on
setlocal
call C:\envs\RTNaBS\Scripts\activate.bat
@echo on
cd "%~dp0"
title %*
python %*
deactivate
endlocal
