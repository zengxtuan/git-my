# 同步 DSH Desktop 的对话与插件清单到官方 DeepSeek Harness 的 home ($HOME\.dsh)
# 源: %APPDATA%\dsh-desktop\harness   目标: $HOME\.dsh
# 先备份，再合并；可重复执行（幂等）。不复制 node_modules。
$ErrorActionPreference = 'Stop'
$src = Join-Path $env:APPDATA 'dsh-desktop\harness'
$dst = Join-Path $HOME '.dsh'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $HOME ('.dsh-backup-' + $stamp)

if (-not (Test-Path $src)) { throw "missing src: $src" }
if (-not (Test-Path $dst)) { throw "missing dst: $dst" }

Write-Output "[1/5] backup -> $backup"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
# sessions + settings + 凭据：整目录复制（无 node_modules）
robocopy (Join-Path $dst 'sessions') (Join-Path $backup 'sessions') /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null
robocopy (Join-Path $dst 'profiles') (Join-Path $backup 'profiles') /E /XD node_modules /NFL /NDL /NJH /NJS /NC /NS | Out-Null
foreach ($f in @('settings.yaml', '.credentials.yaml')) {
  $p = Join-Path $dst $f
  if (Test-Path $p) { Copy-Item $p (Join-Path $backup $f) -Force }
}
$bk = (Get-ChildItem $backup -Recurse -File | Measure-Object)
Write-Output ("      backed up files: " + $bk.Count)

Write-Output "[2/5] copy sessions"
$srcSessions = Join-Path $src 'sessions'
$dstSessions = Join-Path $dst 'sessions'
New-Item -ItemType Directory -Path $dstSessions -Force | Out-Null
robocopy $srcSessions $dstSessions /E /XO /NFL /NDL /NJH /NJS /NC /NS | Out-Null
$s = (Get-ChildItem $dstSessions -Recurse -File | Measure-Object -Property Length -Sum)
Write-Output ("      files=" + $s.Count + " bytes=" + $s.Sum)

Write-Output "[3/5] merge profiles\web\package.json"
$srcPkgPath = Join-Path $src 'profiles\web\package.json'
$dstPkgPath = Join-Path $dst 'profiles\web\package.json'
$srcPkg = Get-Content $srcPkgPath -Raw | ConvertFrom-Json
$dstPkg = Get-Content $dstPkgPath -Raw | ConvertFrom-Json

$mergedDeps = [ordered]@{}
if ($dstPkg.dependencies) { foreach ($k in $dstPkg.dependencies.PSObject.Properties.Name) { $mergedDeps[$k] = $dstPkg.dependencies.$k } }
if ($srcPkg.dependencies) { foreach ($k in $srcPkg.dependencies.PSObject.Properties.Name) { $mergedDeps[$k] = $srcPkg.dependencies.$k } }

$bundles = New-Object System.Collections.Generic.List[string]
foreach ($b in @($dstPkg.dsh.profile.bundles) + @($srcPkg.dsh.profile.bundles)) {
  if ($b -and -not $bundles.Contains($b)) { $bundles.Add($b) }
}

$outPkg = [ordered]@{
  name         = $dstPkg.name
  private      = $true
  dependencies = $mergedDeps
  dsh          = [ordered]@{ profile = [ordered]@{ bundles = @($bundles); patchReload = 'live' } }
}
$outPkg | ConvertTo-Json -Depth 8 | Set-Content -Path $dstPkgPath -Encoding utf8
Write-Output ("      bundles(" + $bundles.Count + "): " + ($bundles -join ', '))
Write-Output ("      deps(" + $mergedDeps.Count + ")")

Write-Output "[4/5] copy profile config layer"
foreach ($name in @('cordis.patch.yml', 'pnpm-workspace.yaml', '.npmrc')) {
  $sp = Join-Path $src "profiles\web\$name"
  if (Test-Path $sp) { Copy-Item $sp (Join-Path $dst "profiles\web\$name") -Force; Write-Output "      copied $name" }
}

Write-Output "[5/5] merge settings.yaml"
$srcSet = Get-Content (Join-Path $src 'settings.yaml') -Raw | ConvertFrom-Json -AsHashtable
$dstSet = Get-Content (Join-Path $dst 'settings.yaml') -Raw | ConvertFrom-Json -AsHashtable
foreach ($k in $srcSet.Keys) { $dstSet[$k] = $srcSet[$k] }
$dstSet | ConvertTo-Json -Depth 10 | Set-Content -Path (Join-Path $dst 'settings.yaml') -Encoding utf8
Write-Output ("      keys: " + (($dstSet.Keys | Sort-Object) -join ', '))

Write-Output ""
Write-Output "BACKUP=$backup"
