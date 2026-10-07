param(
    [Parameter(Mandatory = $true)][string]$LocalConfigDirectory,
    [Parameter(Mandatory = $true)][string]$BundleManifest,
    [Parameter(Mandatory = $true)][string]$WslDistribution,
    [string]$ConfigHome = (Join-Path $env:USERPROFILE '.codex-local'),
    [switch]$DryRun,
    [switch]$Launch
)
$ErrorActionPreference = 'Stop'
$defaultDistro = (& wsl.exe --exec printenv WSL_DISTRO_NAME | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $defaultDistro -ne $WslDistribution) { throw "The GUI uses the default WSL distribution ($defaultDistro). Run installation there, or set the default to $WslDistribution with wsl.exe --set-default." }
$root = Join-Path $env:LOCALAPPDATA 'Programs\Codex Local'
$configHome = $ConfigHome
$guiData = Join-Path $env:LOCALAPPDATA 'Codex Local\User Data'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'Codex Local.lnk'
$marker = Join-Path $root 'codex-local-install.json'
if (Test-Path $root) {
    if (-not (Test-Path $marker)) { throw "Unmanaged application directory: $root" }
    $old = Get-Content $marker -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($old.app -ne $root -or $old.config -ne $configHome) { throw 'Existing installation ownership does not match.' }
} elseif (((Test-Path $configHome) -and -not (Test-Path (Join-Path $configHome 'deployment.json'))) -or (Test-Path $guiData) -or (Test-Path $shortcutPath)) {
    throw 'Existing unmanaged Codex Local data or shortcut; refusing to take ownership.'
}
foreach ($name in @('config.toml', 'model-catalog.json', 'model-instructions.md', 'codex-local-backend.json')) {
    if (-not (Test-Path (Join-Path $LocalConfigDirectory $name))) { throw "Missing $name" }
}
$manifest = Get-Content $BundleManifest -Raw -Encoding UTF8 | ConvertFrom-Json
$archive = Join-Path (Split-Path $BundleManifest) $manifest.asset
if ((Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sha256) { throw 'Windows GUI bundle checksum mismatch.' }
if ($DryRun) { Write-Output "Validated Windows GUI bundle and WSL distribution: $WslDistribution; GUI home: $configHome"; exit 0 }
$stage = "$root.stage-$([Guid]::NewGuid().ToString('N'))"
$backup = "$root.previous-$([Guid]::NewGuid().ToString('N'))"
$utf8 = New-Object System.Text.UTF8Encoding $false
$hadConfigHome = Test-Path $configHome
$hadGuiData = Test-Path $guiData
$savedFiles = @{}
foreach ($path in @((Join-Path $configHome '.codex-global-state.json'), (Join-Path $configHome 'config.toml'), (Join-Path $configHome 'model-catalog.json'), (Join-Path $configHome 'model-instructions.md'), (Join-Path $configHome 'compatibility.json'), $shortcutPath)) {
    $savedFiles[$path] = if (Test-Path $path) { [IO.File]::ReadAllBytes($path) } else { $null }
}
$activated = $false
try {
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    $tar = Join-Path $env:SystemRoot 'System32\tar.exe'
    & $tar -xzf $archive -C $stage
    if ($LASTEXITCODE -ne 0) { throw "GUI archive extraction failed: $LASTEXITCODE" }
    foreach ($name in @('ChatGPT.exe', 'resources\codex', 'resources\app.asar')) {
        if (-not (Test-Path (Join-Path $stage $name))) { throw "Invalid GUI bundle: missing $name" }
    }
    function ConvertTo-WslPath([string]$Path) {
        $result = (& wsl.exe -d $WslDistribution --exec wslpath -u $Path | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $result) { throw "Could not translate installer path: $Path" }
        return $result
    }
    $mergedConfigPath = Join-Path $stage 'managed.config.toml'
    & wsl.exe -d $WslDistribution --exec python3 (ConvertTo-WslPath (Join-Path $PSScriptRoot 'merge_gui_config.py')) `
        --existing (ConvertTo-WslPath (Join-Path $configHome 'config.toml')) `
        --rendered (ConvertTo-WslPath (Join-Path $LocalConfigDirectory 'config.toml')) `
        --output (ConvertTo-WslPath $mergedConfigPath)
    if ($LASTEXITCODE -ne 0) { throw 'Could not merge GUI provider configuration.' }
    $config = Get-Content $mergedConfigPath -Raw -Encoding UTF8
    Remove-Item $mergedConfigPath
    $mergedStatePath = Join-Path $stage 'managed.state.json'
    & wsl.exe -d $WslDistribution --exec python3 (ConvertTo-WslPath (Join-Path $PSScriptRoot 'gui_preferences.py')) `
        --existing (ConvertTo-WslPath (Join-Path $configHome '.codex-global-state.json')) `
        --output (ConvertTo-WslPath $mergedStatePath)
    if ($LASTEXITCODE -ne 0) { throw 'Could not merge GUI reasoning preferences.' }
    $preferences = Get-Content $mergedStatePath -Raw -Encoding UTF8
    Remove-Item $mergedStatePath
    Copy-Item (Join-Path $PSScriptRoot 'codex-local-gui-wsl') (Join-Path $stage 'resources\codex-local-gui-wsl')
    Copy-Item (Join-Path $PSScriptRoot 'gui_backend.py') (Join-Path $stage 'resources\gui_backend.py')
    Copy-Item (Join-Path $LocalConfigDirectory 'codex-local-backend.json') (Join-Path $stage 'resources\codex-local-backend.json')
    $ini = Join-Path $stage 'resources\owl-app.ini'
    if (Test-Path $ini) {
        $text = (Get-Content $ini -Raw -Encoding UTF8).Replace('UserDataDirectoryName=Codex', 'UserDataDirectoryName=Codex Local')
        [IO.File]::WriteAllText($ini, $text, $utf8)
    }
    $launcher = @'
$ErrorActionPreference = 'Stop'
$env:CODEX_HOME = __CONFIG_HOME__
$env:CODEX_ELECTRON_USER_DATA_PATH = Join-Path $env:LOCALAPPDATA 'Codex Local\User Data'
$expectedDistro = __DISTRO__
$actualDistro = (& wsl.exe --exec printenv WSL_DISTRO_NAME | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $actualDistro -ne $expectedDistro) { throw "Codex Local requires the default WSL distribution to be $expectedDistro." }
Remove-Item Env:WSL_DISTRO_NAME -ErrorAction SilentlyContinue
$env:CODEX_CLI_PATH = Join-Path $PSScriptRoot 'resources\codex-local-gui-wsl'
foreach ($name in @('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_OSS_BASE_URL', 'CODEX_OSS_PORT', 'CODEX_APP_SERVER_WS_URL', 'CODEX_APP_SERVER_USE_LOCAL_DAEMON')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
$env:CODEX_APP_SERVER_FORCE_CLI = '1'
Start-Process -FilePath (Join-Path $PSScriptRoot 'ChatGPT.exe') -WorkingDirectory $PSScriptRoot -ArgumentList ('--user-data-dir="' + $env:CODEX_ELECTRON_USER_DATA_PATH + '"')
'@
    $launcher = $launcher.Replace('__DISTRO__', ("'" + $WslDistribution.Replace("'", "''") + "'"))
    $launcher = $launcher.Replace('__CONFIG_HOME__', ("'" + $configHome.Replace("'", "''") + "'"))
    [IO.File]::WriteAllText((Join-Path $stage 'Launch-CodexLocal.ps1'), $launcher, $utf8)
    [ordered]@{ sourcePackage = $manifest.version; app = $root; config = $configHome; userData = $guiData; shortcut = $shortcutPath; sha256 = $manifest.sha256; wslDistribution = $WslDistribution } |
        ConvertTo-Json | Set-Content (Join-Path $stage 'codex-local-install.json') -Encoding UTF8
    # No mutation of a running application's files: rename fails safely if Windows locks it.
    if (Test-Path $root) { Move-Item $root $backup }
    try { Move-Item $stage $root } catch {
        if (Test-Path $backup) { Move-Item $backup $root }
        throw
    }
    $activated = $true
    New-Item -ItemType Directory -Path $configHome, $guiData -Force | Out-Null
    foreach ($name in @('model-catalog.json', 'model-instructions.md')) { Copy-Item (Join-Path $LocalConfigDirectory $name) $configHome -Force }
    $compatibility = Join-Path $LocalConfigDirectory 'compatibility.json'
    if (Test-Path $compatibility) { Copy-Item $compatibility $configHome -Force }
    $configPath = Join-Path $configHome 'config.toml'
    $config = [regex]::Replace($config, '(?m)^runCodexInWindowsSubsystemForLinux\s*=.*\r?\n?', '')
    $config = [regex]::Replace($config, '(?m)^integratedTerminalShell\s*=.*\r?\n?', '')
    $desktop = "[desktop]`nrunCodexInWindowsSubsystemForLinux = true`nintegratedTerminalShell = `"wsl`""
    if ($config -match '(?m)^\[desktop\]\s*$') { $config = [regex]::Replace($config, '(?m)^\[desktop\]\s*$', $desktop) }
    else { $config += "`n$desktop`n" }
    [IO.File]::WriteAllText($configPath, $config, $utf8)
    [IO.File]::WriteAllText((Join-Path $configHome '.codex-global-state.json'), $preferences, $utf8)
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $root 'Launch-CodexLocal.ps1') + '"'
    $shortcut.WorkingDirectory = $env:USERPROFILE
    $shortcut.IconLocation = Join-Path $root 'ChatGPT.exe'
    $shortcut.Description = 'Codex Local with the self-hosted provider and WSL backend'
    $shortcut.Save()
    Write-Output "Installed: $shortcutPath; GUI home: $configHome; WSL distribution: $WslDistribution"
} catch {
    if ($activated) {
        if (Test-Path $root) { Remove-Item $root -Recurse -Force }
        if (Test-Path $backup) { Move-Item $backup $root }
        foreach ($path in $savedFiles.Keys) {
            if ($null -eq $savedFiles[$path]) { Remove-Item $path -Force -ErrorAction SilentlyContinue }
            else { [IO.File]::WriteAllBytes($path, $savedFiles[$path]) }
        }
        if (-not $hadConfigHome -and (Test-Path $configHome)) { Remove-Item $configHome -Recurse -Force }
        if (-not $hadGuiData -and (Test-Path $guiData)) { Remove-Item $guiData -Recurse -Force }
    }
    throw
} finally {
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
}

if (Test-Path $backup) {
    try { Remove-Item $backup -Recurse -Force } catch { Write-Warning "Installed successfully; could not remove old runtime $backup : $_" }
}
if ($Launch) { & (Join-Path $root 'Launch-CodexLocal.ps1') }
