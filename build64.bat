@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat" 2>nul
if errorlevel 1 call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" 2>nul
if errorlevel 1 call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" 2>nul
if errorlevel 1 call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat" 2>nul
if errorlevel 1 (
    echo Could not find Visual Studio environment
    exit /b 1
)
cl /nologo /EHsc /LD main.cpp /I"..\steamaudio\steamaudio\include" "..\steamaudio\steamaudio\lib\windows-x64\phonon.lib" /link /out:steam_audio.dll
if errorlevel 1 (
    echo Build failed
    exit /b 1
)
echo Build successful
copy /y steam_audio.dll addon\globalPlugins\audiothemes\
copy /y "..\steamaudio\steamaudio\lib\windows-x64\phonon.dll" addon\globalPlugins\audiothemes\
