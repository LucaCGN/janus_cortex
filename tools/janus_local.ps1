[CmdletBinding()]
param(
    [ValidateSet("status", "ensure", "track-path", "archive-path", "export-stash", "clean-generated")]
    [string]$Action = "status",
    [string]$SourcePath,
    [string]$Name,
    [string]$StashRef = "stash@{0}",
    [string]$LocalRoot = "",
    [switch]$DropAfterExport
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-DefaultLocalRoot {
    if ($env:JANUS_LOCAL_ROOT) {
        return $env:JANUS_LOCAL_ROOT
    }
    return "C:\code-personal\janus-local\janus_cortex"
}

function Get-ResolvedLocalRoot {
    param([string]$Candidate)

    if ([string]::IsNullOrWhiteSpace($Candidate)) {
        return (Get-DefaultLocalRoot)
    }
    return $Candidate
}

function Get-JanusLayout {
    param([string]$Root)

    return [ordered]@{
        Root = $Root
        Tracks = Join-Path $Root "tracks"
        Archives = Join-Path $Root "archives"
        Stashes = Join-Path $Root "stashes"
        DevCheckpoint = Join-Path $Root "tracks\dev-checkpoint"
        Reference = Join-Path $Root "tracks\reference"
        Output = Join-Path $Root "archives\output"
    }
}

function Ensure-JanusLayout {
    param([hashtable]$Layout)

    foreach ($path in $Layout.Values) {
        if (-not (Test-Path -LiteralPath $path)) {
            New-Item -ItemType Directory -Force -Path $path | Out-Null
        }
    }
}

function Get-UniquePath {
    param([string]$TargetPath)

    if (-not (Test-Path -LiteralPath $TargetPath)) {
        return $TargetPath
    }

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $leaf = Split-Path -Leaf $TargetPath
    $parent = Split-Path -Parent $TargetPath
    return (Join-Path $parent ("{0}_{1}" -f $leaf, $stamp))
}

function Require-Name {
    param([string]$Value, [string]$Fallback)

    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        return $Value
    }
    if (-not [string]::IsNullOrWhiteSpace($Fallback)) {
        return $Fallback
    }
    throw "A name is required for this action."
}

function Move-IntoBucket {
    param(
        [string]$ResolvedSource,
        [string]$BucketRoot,
        [string]$EntryName
    )

    $target = Get-UniquePath (Join-Path $BucketRoot $EntryName)
    Move-Item -LiteralPath $ResolvedSource -Destination $target
    return $target
}

function Export-Stash {
    param(
        [string]$RepositoryRoot,
        [string]$TargetRoot,
        [string]$Reference,
        [string]$EntryName,
        [switch]$DropAfterSave
    )

    $base = Join-Path $TargetRoot $EntryName
    $patchPath = "{0}.patch" -f $base
    $statPath = "{0}.stat.txt" -f $base
    $nameStatusPath = "{0}.name-status.txt" -f $base
    $commitPath = "{0}.commit.txt" -f $base

    git -C $RepositoryRoot stash show -p --include-untracked -- $Reference | Set-Content -Encoding UTF8 $patchPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to export stash patch." }

    git -C $RepositoryRoot stash show --stat --include-untracked -- $Reference | Set-Content -Encoding UTF8 $statPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to export stash stat." }

    git -C $RepositoryRoot stash show --name-status --include-untracked -- $Reference | Set-Content -Encoding UTF8 $nameStatusPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to export stash name-status." }

    git -C $RepositoryRoot rev-parse --verify $Reference | Set-Content -Encoding UTF8 $commitPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to resolve stash commit." }

    if ($DropAfterSave) {
        git -C $RepositoryRoot stash drop $Reference
        if ($LASTEXITCODE -ne 0) { throw "Failed to drop exported stash." }
    }

    return @($patchPath, $statPath, $nameStatusPath, $commitPath)
}

$repoRoot = (Get-Location).Path
$resolvedLocalRoot = Get-ResolvedLocalRoot -Candidate $LocalRoot
$layout = Get-JanusLayout -Root $resolvedLocalRoot

switch ($Action) {
    "ensure" {
        Ensure-JanusLayout -Layout $layout
        Write-Output ("Janus local root ready: {0}" -f $layout.Root)
        Write-Output ("tracks: {0}" -f $layout.Tracks)
        Write-Output ("archives: {0}" -f $layout.Archives)
        Write-Output ("stashes: {0}" -f $layout.Stashes)
    }

    "status" {
        Ensure-JanusLayout -Layout $layout
        Write-Output ("repo_root={0}" -f $repoRoot)
        Write-Output ("local_root={0}" -f $layout.Root)
        foreach ($item in @(
            @{ Label = "tracks"; Path = $layout.Tracks },
            @{ Label = "archives"; Path = $layout.Archives },
            @{ Label = "stashes"; Path = $layout.Stashes },
            @{ Label = "dev_checkpoint"; Path = $layout.DevCheckpoint },
            @{ Label = "reference"; Path = $layout.Reference },
            @{ Label = "output"; Path = $layout.Output }
        )) {
            Write-Output ("{0}={1}" -f $item.Label, $item.Path)
        }
        foreach ($repoPath in @("dev-checkpoint", "reference", "output", ".playwright-cli", ".pytest_cache")) {
            Write-Output ("repo_has_{0}={1}" -f ($repoPath -replace "[^a-zA-Z0-9]+", "_"), (Test-Path -LiteralPath (Join-Path $repoRoot $repoPath)))
        }
    }

    "track-path" {
        Ensure-JanusLayout -Layout $layout
        if ([string]::IsNullOrWhiteSpace($SourcePath)) {
            throw "SourcePath is required for track-path."
        }
        $resolvedSource = (Resolve-Path -LiteralPath $SourcePath).Path
        $entryName = Require-Name -Value $Name -Fallback (Split-Path -Leaf $resolvedSource)
        $target = Move-IntoBucket -ResolvedSource $resolvedSource -BucketRoot $layout.Tracks -EntryName $entryName
        Write-Output ("Moved to tracks: {0}" -f $target)
    }

    "archive-path" {
        Ensure-JanusLayout -Layout $layout
        if ([string]::IsNullOrWhiteSpace($SourcePath)) {
            throw "SourcePath is required for archive-path."
        }
        $resolvedSource = (Resolve-Path -LiteralPath $SourcePath).Path
        $entryName = Require-Name -Value $Name -Fallback (Split-Path -Leaf $resolvedSource)
        $target = Move-IntoBucket -ResolvedSource $resolvedSource -BucketRoot $layout.Archives -EntryName $entryName
        Write-Output ("Moved to archives: {0}" -f $target)
    }

    "export-stash" {
        Ensure-JanusLayout -Layout $layout
        $entryName = Require-Name -Value $Name -Fallback ((Get-Date -Format "yyyy-MM-dd") + "_stash")
        $files = Export-Stash -RepositoryRoot $repoRoot -TargetRoot $layout.Stashes -Reference $StashRef -EntryName $entryName -DropAfterSave:$DropAfterExport
        $files | ForEach-Object { Write-Output ("Saved: {0}" -f $_) }
    }

    "clean-generated" {
        foreach ($cachePath in @(".playwright-cli", ".pytest_cache")) {
            $resolved = Join-Path $repoRoot $cachePath
            if (Test-Path -LiteralPath $resolved) {
                Remove-Item -LiteralPath $resolved -Recurse -Force
                Write-Output ("Removed: {0}" -f $resolved)
            }
        }
        foreach ($guardedPath in @("dev-checkpoint", "reference", "output")) {
            $resolved = Join-Path $repoRoot $guardedPath
            if (Test-Path -LiteralPath $resolved) {
                Write-Output ("Retained for manual move: {0}" -f $resolved)
            }
        }
    }
}
