param(
    [ValidateSet('disposable', 'dev_clone')]
    [string]$Target = 'disposable',
    [string]$Season = '2025-26',
    [string]$SeasonPhase = 'regular_season',
    [string]$AnalysisVersion = 'v1_0_1',
    [string]$OutputRoot = '',
    [switch]$SkipDbSmoke,
    [switch]$SkipDisposableReset,
    [switch]$RebuildMart
)

$ErrorActionPreference = 'Stop'
if ($null -ne (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue)) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DefaultLocalRoot = 'C:\code-personal\janus-local\janus_cortex'
$LocalRoot = if ($env:JANUS_LOCAL_ROOT) { $env:JANUS_LOCAL_ROOT } else { $DefaultLocalRoot }

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $OutputRoot = Join-Path $LocalRoot "archives\output\nba_analysis_validation\$stamp"
}
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$LogDir = Join-Path $OutputRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Set-TargetEnv {
    param(
        [string]$TargetName
    )

    if ($TargetName -eq 'disposable') {
        $env:JANUS_DB_TARGET = 'disposable'
        $env:JANUS_POSTGRES_HOST = '127.0.0.1'
        $env:JANUS_POSTGRES_PORT = '55432'
        $env:JANUS_POSTGRES_DB = 'janus_disposable'
        $env:JANUS_POSTGRES_USER = 'janus'
        $env:JANUS_POSTGRES_PASSWORD = 'janus'
        return
    }

    if ([string]::IsNullOrWhiteSpace($env:JANUS_POSTGRES_HOST) -or
        [string]::IsNullOrWhiteSpace($env:JANUS_POSTGRES_PORT) -or
        [string]::IsNullOrWhiteSpace($env:JANUS_POSTGRES_DB) -or
        [string]::IsNullOrWhiteSpace($env:JANUS_POSTGRES_USER) -or
        [string]::IsNullOrWhiteSpace($env:JANUS_POSTGRES_PASSWORD)) {
        throw 'Target dev_clone requires JANUS_POSTGRES_HOST, JANUS_POSTGRES_PORT, JANUS_POSTGRES_DB, JANUS_POSTGRES_USER, and JANUS_POSTGRES_PASSWORD to already be set.'
    }
    $env:JANUS_DB_TARGET = 'dev_clone'
}

function Invoke-LoggedCommand {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$CommandArgs
    )

    $stdoutPath = Join-Path $LogDir "$Name.stdout.log"
    $stderrPath = Join-Path $LogDir "$Name.stderr.log"
    $startAt = Get-Date

    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $CommandArgs `
        -WorkingDirectory $RepoRoot `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath
    $exitCode = $process.ExitCode

    $duration = [math]::Round(((Get-Date) - $startAt).TotalSeconds, 2)
    return [pscustomobject]@{
        name = $Name
        file_path = $FilePath
        command_args = $CommandArgs
        exit_code = $exitCode
        ok = ($exitCode -eq 0)
        duration_seconds = $duration
        stdout = $stdoutPath
        stderr = $stderrPath
    }
}

function Read-JsonLog {
    param(
        [string]$Path
    )

    $text = Get-Content -Raw -Path $Path
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }
    return $text | ConvertFrom-Json
}

$oldRunDbTests = $env:JANUS_RUN_DB_TESTS
$oldTarget = $env:JANUS_DB_TARGET
$oldHost = $env:JANUS_POSTGRES_HOST
$oldPort = $env:JANUS_POSTGRES_PORT
$oldDb = $env:JANUS_POSTGRES_DB
$oldUser = $env:JANUS_POSTGRES_USER
$oldPassword = $env:JANUS_POSTGRES_PASSWORD

try {
    if ($Target -eq 'disposable' -and -not $SkipDisposableReset) {
        & powershell -ExecutionPolicy Bypass -File (Join-Path $RepoRoot 'tools\janus_db.ps1') reset-disposable
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to reset disposable Postgres before validation.'
        }
    }

    Set-TargetEnv -TargetName $Target
    $env:JANUS_RUN_DB_TESTS = '1'

    $commands = New-Object System.Collections.Generic.List[object]

    if ($Target -eq 'disposable' -and -not $SkipDbSmoke) {
        $commands.Add((Invoke-LoggedCommand -Name 'db_smoke' -FilePath 'powershell' -CommandArgs @('-ExecutionPolicy', 'Bypass', '-File', '.\tools\janus_db.ps1', 'smoke-disposable')))
    }

    $commands.Add((Invoke-LoggedCommand -Name 'describe_target' -FilePath 'python' -CommandArgs @('-m', 'app.data.databases.migrate', '--describe-target')))
    $commands.Add((Invoke-LoggedCommand -Name 'analysis_pytest_sweep' -FilePath 'python' -CommandArgs @('-m', 'pytest', '-q', 'tests/app/api/test_analysis_studio_router_pytest.py', 'tests/app/data/pipelines/daily/nba/test_analysis_universe_pytest.py', 'tests/app/data/pipelines/daily/nba/test_analysis_mart_game_profiles_pytest.py', 'tests/app/data/pipelines/daily/nba/test_analysis_module_pytest.py', 'tests/app/data/pipelines/daily/nba/test_analysis_models_pytest.py', 'tests/app/data/pipelines/daily/nba/test_analysis_consumer_adapters_pytest.py')))

    $martArgs = @('-m', 'app.data.pipelines.daily.nba.analysis_module', 'build_analysis_mart', '--season', $Season, '--season-phase', $SeasonPhase, '--analysis-version', $AnalysisVersion, '--output-root', $OutputRoot)
    if ($RebuildMart -or $Target -eq 'disposable') {
        $martArgs += '--rebuild'
    }
    $commands.Add((Invoke-LoggedCommand -Name 'build_analysis_mart' -FilePath 'python' -CommandArgs $martArgs))
    $commands.Add((Invoke-LoggedCommand -Name 'build_analysis_report' -FilePath 'python' -CommandArgs @('-m', 'app.data.pipelines.daily.nba.analysis_module', 'build_analysis_report', '--season', $Season, '--season-phase', $SeasonPhase, '--analysis-version', $AnalysisVersion, '--output-root', $OutputRoot)))
    $commands.Add((Invoke-LoggedCommand -Name 'run_analysis_backtests' -FilePath 'python' -CommandArgs @('-m', 'app.data.pipelines.daily.nba.analysis_module', 'run_analysis_backtests', '--season', $Season, '--season-phase', $SeasonPhase, '--analysis-version', $AnalysisVersion, '--output-root', $OutputRoot)))
    $commands.Add((Invoke-LoggedCommand -Name 'train_analysis_baselines' -FilePath 'python' -CommandArgs @('-m', 'app.data.pipelines.daily.nba.analysis_module', 'train_analysis_baselines', '--season', $Season, '--season-phase', $SeasonPhase, '--analysis-version', $AnalysisVersion, '--output-root', $OutputRoot)))
    $commands.Add((Invoke-LoggedCommand -Name 'collect_validation_snapshot' -FilePath 'python' -CommandArgs @('.\tools\collect_analysis_validation_snapshot.py', '--season', $Season, '--season-phase', $SeasonPhase, '--analysis-version', $AnalysisVersion, '--output-root', $OutputRoot)))

    $parsedOutputs = @{}
    foreach ($command in $commands) {
        if ($command.ok -and $command.stdout -like '*.log') {
            if ($command.name -in @('describe_target', 'build_analysis_mart', 'build_analysis_report', 'run_analysis_backtests', 'train_analysis_baselines', 'collect_validation_snapshot')) {
                $parsedOutputs[$command.name] = Read-JsonLog -Path $command.stdout
            }
        }
    }

    $payload = [ordered]@{
        target = $Target
        season = $Season
        season_phase = $SeasonPhase
        analysis_version = $AnalysisVersion
        output_root = $OutputRoot
        log_dir = $LogDir
        all_commands_ok = -not ($commands | Where-Object { -not $_.ok })
        commands = $commands
        parsed_outputs = $parsedOutputs
    }

    $jsonPath = Join-Path $OutputRoot 'validation_summary.json'
    $payload | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 -Path $jsonPath

    $lines = @(
        '# NBA Analysis Validation Summary',
        '',
        "- Target: $Target",
        "- Season: $Season",
        "- Season phase: $SeasonPhase",
        "- Analysis version: $AnalysisVersion",
        "- Output root: $OutputRoot",
        "- All commands ok: $(if ($payload.all_commands_ok) { 'true' } else { 'false' })",
        '',
        '## Commands',
        ''
    )
    foreach ($command in $commands) {
        $lines += "- $($command.name): exit=$($command.exit_code) duration_seconds=$($command.duration_seconds) stdout=$($command.stdout) stderr=$($command.stderr)"
    }
    if ($parsedOutputs.ContainsKey('collect_validation_snapshot')) {
        $snapshot = $parsedOutputs['collect_validation_snapshot']
        $lines += ''
        $lines += '## Universe Snapshot'
        $lines += ''
        $lines += "- Games total: $($snapshot.universe.games_total)"
        $lines += "- Research-ready games: $($snapshot.universe.research_ready_games)"
        $lines += "- Descriptive-only games: $($snapshot.universe.descriptive_only_games)"
        $lines += "- Excluded games: $($snapshot.universe.excluded_games)"
        if ($null -ne $snapshot.consumer_snapshot) {
            $lines += ''
            $lines += '## Consumer Snapshot'
            $lines += ''
            $lines += "- Output dir: $($snapshot.consumer_snapshot.output_dir)"
            $lines += "- Benchmark contract version: $($snapshot.consumer_snapshot.benchmark_contract_version)"
            $lines += "- Benchmark experiment id: $($snapshot.consumer_snapshot.benchmark_experiment_id)"
            $lines += "- Report sections: $($snapshot.consumer_snapshot.report_section_count)"
            $lines += "- Model tracks: $($snapshot.consumer_snapshot.model_track_count)"
        }
    }
    $markdownPath = Join-Path $OutputRoot 'validation_summary.md'
    $lines | Set-Content -Encoding utf8 -Path $markdownPath

    Write-Output "validation_json=$jsonPath"
    Write-Output "validation_markdown=$markdownPath"
    if (-not $payload.all_commands_ok) {
        exit 1
    }
}
finally {
    $env:JANUS_RUN_DB_TESTS = $oldRunDbTests
    $env:JANUS_DB_TARGET = $oldTarget
    $env:JANUS_POSTGRES_HOST = $oldHost
    $env:JANUS_POSTGRES_PORT = $oldPort
    $env:JANUS_POSTGRES_DB = $oldDb
    $env:JANUS_POSTGRES_USER = $oldUser
    $env:JANUS_POSTGRES_PASSWORD = $oldPassword
}
