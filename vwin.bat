@echo off
:: Run the python executable directly from the venv
:: %* passes all arguments from the .bat to the .py script
.\venv\Scripts\python.exe vivid.py %*

pause