$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $projectRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'launch.py')
if ($LASTEXITCODE -ne 0) { throw 'FF Agent failed to start. See reports/server.log.' }
