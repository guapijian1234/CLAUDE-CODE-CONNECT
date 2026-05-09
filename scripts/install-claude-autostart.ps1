param(
    [switch]$Uninstall,
    [switch]$SkipExecutionPolicy,
    [switch]$ProjectOnly
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$ClaudeConfigDir = Join-Path $HOME ".claude"
$GlobalMcpPath = Join-Path $HOME ".claude.json"
$WrapperDir = Join-Path $HOME "bin"
$WrapperCmd = Join-Path $WrapperDir "claude.cmd"
$WrapperPs1 = Join-Path $WrapperDir "claude-qq-wrapper.ps1"
$ProfilePath = $PROFILE
$ProfileDir = Split-Path -Parent $ProfilePath
$StartMarker = "# >>> claude-code-connect qq autostart >>>"
$EndMarker = "# <<< claude-code-connect qq autostart <<<"

function Resolve-RealClaude {
    $commands = Get-Command claude -All -CommandType Application -ErrorAction Stop
    foreach ($command in $commands) {
        $source = $command.Source
        if ([string]::IsNullOrWhiteSpace($source)) {
            continue
        }

        try {
            $resolved = (Resolve-Path -LiteralPath $source -ErrorAction Stop).Path
        } catch {
            $resolved = $source
        }

        if ($resolved -ieq $WrapperCmd -or $resolved -ieq $WrapperPs1) {
            continue
        }
        if ([System.IO.Path]::GetExtension($resolved) -ieq ".exe") {
            return $resolved
        }
    }

    foreach ($command in $commands) {
        $source = $command.Source
        if ([string]::IsNullOrWhiteSpace($source)) {
            continue
        }

        try {
            $resolved = (Resolve-Path -LiteralPath $source -ErrorAction Stop).Path
        } catch {
            $resolved = $source
        }

        if ($resolved -ine $WrapperCmd -and $resolved -ine $WrapperPs1) {
            return $resolved
        }
    }

    throw "Could not find the real Claude Code executable. Install Claude Code first, then rerun this script."
}

$RealClaude = Resolve-RealClaude

if (-not (Test-Path -LiteralPath $ProfileDir)) {
    New-Item -ItemType Directory -Path $ProfileDir | Out-Null
}
if (-not (Test-Path -LiteralPath $ClaudeConfigDir)) {
    New-Item -ItemType Directory -Path $ClaudeConfigDir | Out-Null
}
if (-not (Test-Path -LiteralPath $WrapperDir)) {
    New-Item -ItemType Directory -Path $WrapperDir | Out-Null
}

function Install-McpServer {
    & python -m pip install -e $ProjectRoot | Out-Host
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $RealClaude mcp remove -s user qq-bridge *> $null
    $ErrorActionPreference = $oldErrorActionPreference
    & $RealClaude mcp add -s user qq-bridge -- python -m qq_bridge mcp | Out-Host
}

function Uninstall-McpServer {
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $RealClaude mcp remove -s user qq-bridge *> $null
    $ErrorActionPreference = $oldErrorActionPreference
}

function Install-PathWrapper {
    $wrapper = @"
`$Arguments = @(`$args)
`$realClaude = '$RealClaude'
`$managementCommands = @(
    'agents', 'auth', 'auto-mode', 'doctor', 'install', 'mcp',
    'plugin', 'plugins', 'project', 'setup-token', 'ultrareview',
    'update', 'upgrade'
)

`$hasChannels = `$Arguments -contains '--channels' -or `$Arguments -contains '--dangerously-load-development-channels'
`$wantsHelpOnly = `$Arguments -contains '-h' -or `$Arguments -contains '--help' -or `$Arguments -contains '-v' -or `$Arguments -contains '--version'
`$firstPlainArg = `$Arguments | Where-Object { `$null -ne `$_ -and -not `$_.StartsWith('-') } | Select-Object -First 1
`$isManagementCommand = `$firstPlainArg -and (`$managementCommands -contains `$firstPlainArg)

if (-not `$hasChannels -and -not `$wantsHelpOnly -and -not `$isManagementCommand) {
    & `$realClaude --dangerously-load-development-channels server:qq-bridge @Arguments
} else {
    & `$realClaude @Arguments
}
exit `$LASTEXITCODE
"@

    Set-Content -LiteralPath $WrapperPs1 -Value $wrapper -Encoding UTF8
    $cmd = "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"$WrapperPs1`" %*`r`n"
    Set-Content -LiteralPath $WrapperCmd -Value $cmd -Encoding ASCII

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if (-not [string]::IsNullOrWhiteSpace($userPath)) {
        $parts = $userPath -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    }
    $hasWrapperDir = $parts | Where-Object { $_.TrimEnd('\') -ieq $WrapperDir.TrimEnd('\') }
    if (-not $hasWrapperDir) {
        $newPath = ($WrapperDir, $userPath) -join ';'
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        $env:Path = ($WrapperDir, $env:Path) -join ';'
    }
}

function Uninstall-PathWrapper {
    Remove-Item -LiteralPath $WrapperCmd -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $WrapperPs1 -Force -ErrorAction SilentlyContinue

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not [string]::IsNullOrWhiteSpace($userPath)) {
        $parts = $userPath -split ';' | Where-Object {
            -not [string]::IsNullOrWhiteSpace($_) -and $_.TrimEnd('\') -ine $WrapperDir.TrimEnd('\')
        }
        [Environment]::SetEnvironmentVariable("Path", ($parts -join ';'), "User")
    }
}

$existing = ""
if (Test-Path -LiteralPath $ProfilePath) {
    $existing = Get-Content -Raw -LiteralPath $ProfilePath
}

$pattern = [regex]::Escape($StartMarker) + "(?s).*?" + [regex]::Escape($EndMarker) + "\r?\n?"
$cleaned = [regex]::Replace($existing, $pattern, "")

if ($Uninstall) {
    Set-Content -LiteralPath $ProfilePath -Value $cleaned -Encoding UTF8
    Uninstall-McpServer
    Uninstall-PathWrapper
    Write-Host "Removed Claude QQ autostart from $ProfilePath"
    Write-Host "Removed qq-bridge from $GlobalMcpPath"
    Write-Host "Removed PATH wrapper from $WrapperDir"
    exit 0
}

if (-not $SkipExecutionPolicy) {
    $currentUserPolicy = Get-ExecutionPolicy -Scope CurrentUser
    if ($currentUserPolicy -notin @("RemoteSigned", "Unrestricted", "Bypass")) {
        powershell -NoProfile -Command "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force" | Out-Null
        Write-Host "Set PowerShell CurrentUser execution policy to RemoteSigned so the profile can load."
    }
}

$block = @"
$StartMarker
function claude {
    `$Arguments = @(`$args)
    `$projectRoot = '$ProjectRoot'
    `$realClaude = '$RealClaude'
    `$projectOnly = `$$($ProjectOnly.IsPresent.ToString().ToLowerInvariant())
    `$managementCommands = @(
        'agents', 'auth', 'auto-mode', 'doctor', 'install', 'mcp',
        'plugin', 'plugins', 'project', 'setup-token', 'ultrareview',
        'update', 'upgrade'
    )

    try {
    `$current = (Resolve-Path -LiteralPath (Get-Location)).Path
        `$root = (Resolve-Path -LiteralPath `$projectRoot).Path
        `$insideProject =
            `$current.Equals(`$root, [System.StringComparison]::OrdinalIgnoreCase) -or
            `$current.StartsWith(`$root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        `$insideProject = `$false
    }

    `$hasChannels = `$Arguments -contains '--channels' -or `$Arguments -contains '--dangerously-load-development-channels'
    `$wantsHelpOnly = `$Arguments -contains '-h' -or `$Arguments -contains '--help' -or `$Arguments -contains '-v' -or `$Arguments -contains '--version'
    `$firstPlainArg = `$Arguments | Where-Object { `$null -ne `$_ -and -not `$_.StartsWith('-') } | Select-Object -First 1
    `$isManagementCommand = `$firstPlainArg -and (`$managementCommands -contains `$firstPlainArg)
    `$shouldAttachQQ = (-not `$projectOnly -or `$insideProject) -and -not `$hasChannels -and -not `$wantsHelpOnly -and -not `$isManagementCommand

    if (`$shouldAttachQQ) {
        `$prefix = @('--dangerously-load-development-channels', 'server:qq-bridge')
        & `$realClaude @prefix @Arguments
    } else {
        & `$realClaude @Arguments
    }
}
$EndMarker
"@

$newContent = $cleaned.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine + $block + [Environment]::NewLine
Set-Content -LiteralPath $ProfilePath -Value $newContent -Encoding UTF8
Install-McpServer
Install-PathWrapper

Write-Host "Installed Claude QQ autostart into $ProfilePath"
Write-Host "Installed qq-bridge MCP server into $GlobalMcpPath"
Write-Host "Installed PATH wrapper into $WrapperCmd"
if ($ProjectOnly) {
    Write-Host "Scope: this project only"
    Write-Host "Open a new PowerShell window, cd to:"
    Write-Host "  $ProjectRoot"
    Write-Host "Then run:"
    Write-Host "  claude"
} else {
    Write-Host "Scope: all directories"
    Write-Host "Open a new PowerShell window in any project and run:"
    Write-Host "  claude"
}
