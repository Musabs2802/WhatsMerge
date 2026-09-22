# WhatsMerge — rebuild the archive from .\exports into .\output\archive.html
#
#   .\merge.ps1                      normal run
#   .\merge.ps1 -Me "Musab Shaikh"   say who owns the archive
#   .\merge.ps1 -External            keep media beside the HTML, not inside it
#
# Any other whatsmerge flag can be passed through after --, e.g.
#   .\merge.ps1 -- --ticks none --theme dark

[CmdletBinding()]
param(
    [string] $Me,
    [switch] $External,
    [switch] $NoOpen,
    [Parameter(ValueFromRemainingArguments = $true)] $Passthrough
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python was not found on your PATH. Install Python 3.10+ from https://python.org"
    exit 1
}

$argv = @()
if ($Me)        { $argv += @('--me', $Me) }
if ($External)  { $argv += @('--media', 'external') }
if (-not $NoOpen) { $argv += '--open' }
if ($Passthrough) { $argv += $Passthrough }

python -m app @argv
exit $LASTEXITCODE
