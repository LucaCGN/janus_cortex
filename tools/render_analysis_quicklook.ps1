[CmdletBinding()]
param(
    [string]$Season = "2025-26",
    [string]$SeasonPhase = "regular_season",
    [string]$AnalysisVersion = "v1_0_1",
    [string]$OutputRoot = "",
    [string]$SourceDir = "",
    [string]$RenderDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $pythonArgs = @(
        ".\tools\render_analysis_quicklook.py",
        "--season", $Season,
        "--season-phase", $SeasonPhase,
        "--analysis-version", $AnalysisVersion
    )
    if (-not [string]::IsNullOrWhiteSpace($OutputRoot)) {
        $pythonArgs += @("--output-root", $OutputRoot)
    }
    if (-not [string]::IsNullOrWhiteSpace($SourceDir)) {
        $pythonArgs += @("--source-dir", $SourceDir)
    }
    if (-not [string]::IsNullOrWhiteSpace($RenderDir)) {
        $pythonArgs += @("--render-dir", $RenderDir)
    }
    python @pythonArgs
}
finally {
    Pop-Location
}
