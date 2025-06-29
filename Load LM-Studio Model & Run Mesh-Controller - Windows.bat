@echo off
cd /d "%USERPROFILE%\Desktop\meshtastic-controller"  # Your Meshtastic-Controller directory

echo Unloading any previously loaded model before reloading...
lms unload <INSERT MODEL IDENTIFIER HERE>
timeout /t 2 /nobreak >nul

echo Loading defined model...
lms load <INSERT MODEL IDENTIFIER HERE>
timeout /t 5 /nobreak >nul

echo Running Meshtastic-Controller...
python main.py

pause
