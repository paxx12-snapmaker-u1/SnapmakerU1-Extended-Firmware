@echo off
::
:: add_md5.bat — Stamp an MD5 checksum onto a g-code file.
::
:: Usage: add_md5.bat <file.gcode>
::
:: Prepends "; MD5:<hash>" as the very first line of the file.
:: The hash covers everything EXCEPT that header line, so the printer-side
:: Klipper plugin skips the header and hashes the rest identically.
::
:: Run this on your slicer host after slicing and before uploading.
:: It can be added as a post-processing script in your slicer:
::   Snapmaker Orca / OrcaSlicer  : Process -> Others -> Post-processing Scripts
::   PrusaSlicer : Print Settings -> Output options -> Post-processing scripts
::
:: Requires: CertUtil (built into Windows Vista and later — no extras needed).
::
:: Ported from DrA1ex/ff5m (https://github.com/DrA1ex/ff5m).
:: License: GNU GPLv3

setlocal EnableDelayedExpansion

:: ---------------------------------------------------------------------------
:: Validate arguments
:: ---------------------------------------------------------------------------
if "%~1"=="" (
    echo Usage: %~nx0 ^<file.gcode^> >&2
    exit /b 1
)

set "FILE=%~1"

if not exist "%FILE%" (
    echo Error: file not found: %FILE% >&2
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: Compute MD5 via CertUtil (built-in on all modern Windows)
:: ---------------------------------------------------------------------------
for /f "skip=1 tokens=*" %%H in ('CertUtil -hashfile "%FILE%" MD5') do (
    if not defined HASH_LINE set "HASH_LINE=%%H"
)

:: Strip any spaces from the hash line
set "HASH=%HASH_LINE: =%"

if "%HASH%"=="" (
    echo Error: failed to compute MD5 hash. >&2
    exit /b 2
)

:: ---------------------------------------------------------------------------
:: Prepend the header line using a temporary file
:: ---------------------------------------------------------------------------
set "TMPFILE=%FILE%.md5tmp"

echo ; MD5:%HASH%> "%TMPFILE%"
type "%FILE%" >> "%TMPFILE%"

move /y "%TMPFILE%" "%FILE%" >nul

echo Stamped %FILE%: %HASH%

endlocal
