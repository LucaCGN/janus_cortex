param(
    [ValidateSet('status', 'bootstrap-disposable', 'reset-disposable', 'smoke-disposable', 'teardown-disposable', 'print-disposable-env')]
    [string]$Command = 'status'
)

$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DefaultLocalRoot = 'C:\code-personal\janus-local\janus_cortex'
$LocalRoot = if ($env:JANUS_LOCAL_ROOT) { $env:JANUS_LOCAL_ROOT } else { $DefaultLocalRoot }
$ReferenceDir = Join-Path $LocalRoot 'tracks\reference\db'
$EnvFile = Join-Path $ReferenceDir 'disposable_postgres.env'

$ContainerName = 'janus-cortex-postgres-disposable'
$VolumeName = 'janus-cortex-postgres-disposable'
$ImageName = 'postgres:16-alpine'
$DbHost = '127.0.0.1'
$DbPort = '55432'
$DbName = 'janus_disposable'
$DbUser = 'janus'
$DbPassword = 'janus'

function Ensure-ReferenceDir {
    New-Item -ItemType Directory -Force -Path $ReferenceDir | Out-Null
}

function Get-DisposableEnvLines {
    return @(
        'JANUS_DB_TARGET=disposable',
        "JANUS_POSTGRES_HOST=$DbHost",
        "JANUS_POSTGRES_PORT=$DbPort",
        "JANUS_POSTGRES_DB=$DbName",
        "JANUS_POSTGRES_USER=$DbUser",
        "JANUS_POSTGRES_PASSWORD=$DbPassword"
    )
}

function Write-DisposableEnvFile {
    Ensure-ReferenceDir
    Get-DisposableEnvLines | Set-Content -Encoding ascii -Path $EnvFile
}

function Get-ContainerStatus {
    $status = docker ps -a --filter "name=^/$ContainerName$" --format "{{.Status}}" 2>$null
    if (-not $status) {
        return 'missing'
    }
    return ($status | Select-Object -First 1)
}

function Require-DockerAvailable {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker is installed but the daemon is not available. Start Docker Desktop and rerun the command.'
    }
}

function Start-DisposableContainer {
    Require-DockerAvailable
    $status = Get-ContainerStatus
    if ($status -eq 'missing') {
        docker run -d `
            --name $ContainerName `
            -e "POSTGRES_DB=$DbName" `
            -e "POSTGRES_USER=$DbUser" `
            -e "POSTGRES_PASSWORD=$DbPassword" `
            -p "${DbHost}:${DbPort}:5432" `
            -v "${VolumeName}:/var/lib/postgresql/data" `
            $ImageName | Out-Null
        return
    }
    if ($status -notmatch '^Up ') {
        docker start $ContainerName | Out-Null
    }
}

function Wait-ForDisposablePostgres {
    for ($i = 0; $i -lt 30; $i++) {
        docker exec $ContainerName pg_isready -U $DbUser -d $DbName *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Seconds 1
    }
    throw 'Disposable Postgres did not become ready within 30 seconds.'
}

function Invoke-DisposablePython {
    param(
        [string[]]$CommandArgs
    )

    $oldTarget = $env:JANUS_DB_TARGET
    $oldHost = $env:JANUS_POSTGRES_HOST
    $oldPort = $env:JANUS_POSTGRES_PORT
    $oldDb = $env:JANUS_POSTGRES_DB
    $oldUser = $env:JANUS_POSTGRES_USER
    $oldPassword = $env:JANUS_POSTGRES_PASSWORD

    try {
        $env:JANUS_DB_TARGET = 'disposable'
        $env:JANUS_POSTGRES_HOST = $DbHost
        $env:JANUS_POSTGRES_PORT = $DbPort
        $env:JANUS_POSTGRES_DB = $DbName
        $env:JANUS_POSTGRES_USER = $DbUser
        $env:JANUS_POSTGRES_PASSWORD = $DbPassword

        Push-Location $RepoRoot
        & python @CommandArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Python command failed: python $($CommandArgs -join ' ')"
        }
    }
    finally {
        Pop-Location
        $env:JANUS_DB_TARGET = $oldTarget
        $env:JANUS_POSTGRES_HOST = $oldHost
        $env:JANUS_POSTGRES_PORT = $oldPort
        $env:JANUS_POSTGRES_DB = $oldDb
        $env:JANUS_POSTGRES_USER = $oldUser
        $env:JANUS_POSTGRES_PASSWORD = $oldPassword
    }
}

function Bootstrap-Disposable {
    Start-DisposableContainer
    Wait-ForDisposablePostgres
    Write-DisposableEnvFile
    Invoke-DisposablePython -CommandArgs @('-m', 'app.data.databases.migrate', '--drop-managed-schemas', '--require-safe-target')
}

function Reset-Disposable {
    Teardown-Disposable
    Bootstrap-Disposable
}

function Teardown-Disposable {
    docker rm -f $ContainerName *> $null
    docker volume rm $VolumeName *> $null
}

function Smoke-Disposable {
    Bootstrap-Disposable
    $oldRunDbTests = $env:JANUS_RUN_DB_TESTS
    try {
        $env:JANUS_RUN_DB_TESTS = '1'
        Invoke-DisposablePython -CommandArgs @('-m', 'pytest', '-q', 'tests/app/data/databases/test_postgres_migrations_pytest.py', 'tests/app/data/databases/test_upsert_primitives_pytest.py')
    }
    finally {
        $env:JANUS_RUN_DB_TESTS = $oldRunDbTests
    }
}

switch ($Command) {
    'status' {
        Write-Output "repo_root=$RepoRoot"
        Write-Output "local_root=$LocalRoot"
        Write-Output "reference_dir=$ReferenceDir"
        Write-Output "env_file=$EnvFile"
        Write-Output "container_name=$ContainerName"
        Write-Output "container_status=$(Get-ContainerStatus)"
        Write-Output "disposable_target=JANUS_DB_TARGET=disposable"
        Write-Output "disposable_host=$DbHost"
        Write-Output "disposable_port=$DbPort"
        Write-Output "disposable_db=$DbName"
    }
    'bootstrap-disposable' {
        Bootstrap-Disposable
        Write-Output "bootstrap=ok"
        Write-Output "env_file=$EnvFile"
    }
    'reset-disposable' {
        Reset-Disposable
        Write-Output "reset=ok"
        Write-Output "env_file=$EnvFile"
    }
    'smoke-disposable' {
        Smoke-Disposable
        Write-Output 'smoke=ok'
    }
    'teardown-disposable' {
        Teardown-Disposable
        Write-Output 'teardown=ok'
    }
    'print-disposable-env' {
        Write-DisposableEnvFile
        Get-Content $EnvFile
    }
}
