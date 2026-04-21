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
$localRoot = "C:\code-personal\janus-local\janus_cortex"
$defaultSourceDir = Join-Path $localRoot ("archives\output\nba_analysis\{0}\{1}\{2}\backtests" -f $Season, $SeasonPhase, $AnalysisVersion)
$defaultRenderDir = Join-Path $defaultSourceDir "quicklook_png"
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
    $resolvedSourceDir = if (-not [string]::IsNullOrWhiteSpace($SourceDir)) { $SourceDir } else { $defaultSourceDir }
    $resolvedRenderDir = if (-not [string]::IsNullOrWhiteSpace($RenderDir)) { $RenderDir } else { $defaultRenderDir }
    $pythonArgs += @("--source-dir", $resolvedSourceDir)
    $pythonArgs += @("--render-dir", $resolvedRenderDir)
    python @pythonArgs
}
finally {
    Pop-Location
}
