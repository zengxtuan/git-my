#Requires -Version 5.1
<#
.SYNOPSIS
    一键从已连接的 Android 手机导出目标 App 的 APK，存到 samples/。

.DESCRIPTION
    全程只做「读」：查询包名、读取安装路径、拉取文件。
    不改手机、不需要 root、不需要授权任何东西，除了 USB 调试。

.EXAMPLE
    .\tools\pull-apk.ps1
    .\tools\pull-apk.ps1 -Keyword "sport"
#>
param(
    # 用来筛选包名的关键字，多个用逗号分隔
    [string]$Keyword = 'sport,run,keep,fitness,physical,tiyu',

    # 输出目录
    [string]$OutDir = 'samples'
)

# adb 会在 stderr 打「daemon not running」这类提示，不能被当成致命错误
$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent $PSScriptRoot

function Find-Adb {
    $candidates = @(
        (Get-Command adb -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source),
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe",
        "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe",
        "$env:ProgramFiles\Android\platform-tools\adb.exe",
        "$env:USERPROFILE\platform-tools\adb.exe",
        'C:\platform-tools\adb.exe'
    )

    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return [string]$c }
    }

    # winget 安装目录名带随机后缀，兜底扫一遍
    $hit = @(Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter adb.exe -ErrorAction SilentlyContinue)
    if ($hit.Count -gt 0) { return [string]$hit[0].FullName }

    throw "找不到 adb。请先安装 Android Platform-Tools：winget install Google.PlatformTools"
}

$adb = Find-Adb
Write-Host ""
Write-Host "使用 adb: $adb" -ForegroundColor DarkGray

# ---- 1. 检查设备 ----
$deviceLines = @(& $adb devices 2>$null | Select-Object -Skip 1 | Where-Object { $_ -match '\S' })
if ($deviceLines.Count -eq 0) {
    Write-Host ""
    Write-Host "没有检测到手机。请先完成这三步：" -ForegroundColor Yellow
    Write-Host "  1. 设置 → 关于本机 → 版本号，连点 7 次"
    Write-Host "  2. 设置 → 系统设置 → 开发者选项 → 打开 USB 调试"
    Write-Host "  3. 插上数据线，手机上弹出「允许 USB 调试吗」时点【允许】"
    Write-Host ""
    exit 1
}

$unauthorized = @($deviceLines | Where-Object { $_ -match 'unauthorized' })
if ($unauthorized.Count -gt 0) {
    Write-Host "手机已连接，但还没授权。请看着手机屏幕点【允许】。" -ForegroundColor Yellow
    exit 1
}

Write-Host "已连接设备:" -ForegroundColor Green
foreach ($d in $deviceLines) { Write-Host "  $d" }

# ---- 2. 找候选包 ----
Write-Host ""
Write-Host "正在扫描已安装的 App..." -ForegroundColor DarkGray
$allPackages = @((& $adb shell pm list packages 2>$null) -replace '^package:', '' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

$keywords = @($Keyword.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$candidates = @(
    $allPackages | Where-Object {
        $pkg = $_
        $matched = @($keywords | Where-Object { $pkg -match [regex]::Escape($_) })
        $matched.Count -gt 0
    }
)

if ($candidates.Count -eq 0) {
    Write-Host ""
    Write-Host "没匹配到疑似校园跑的 App。全部已安装包如下，可以手动找：" -ForegroundColor Yellow
    foreach ($p in @($allPackages | Where-Object { $_ -notmatch '^android\.' } | Sort-Object)) { Write-Host "  $p" }
    Write-Host ""
    Write-Host "找到目标包名后，可用包名里的关键字重新指定："
    Write-Host "  .\tools\pull-apk.ps1 -Keyword 关键字" -ForegroundColor Cyan
    exit 2
}

Write-Host ""
Write-Host "疑似目标 App：" -ForegroundColor Green
$i = 1
foreach ($c in $candidates) { Write-Host ("  [{0}] {1}" -f $i++, $c) }

$choice = 1
if ($candidates.Count -gt 1) {
    $userInput = Read-Host "`n输入序号选择（直接回车 = 1）"
    if ($userInput -and $userInput -match '^\d+$') {
        $n = [int]$userInput
        if ($n -ge 1 -and $n -le $candidates.Count) { $choice = $n }
    }
}
$pkg = [string]$candidates[$choice - 1]
Write-Host "已选择: $pkg" -ForegroundColor Cyan

# ---- 3. 读安装路径（可能是 split APK，有多行）----
$paths = @((& $adb shell pm path $pkg 2>$null) -replace '^package:', '' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($paths.Count -eq 0) {
    throw "拿不到 $pkg 的安装路径。该 App 可能没有为当前用户安装。"
}

# ---- 4. 拉取 ----
$dest = Join-Path $repoRoot $OutDir
New-Item -ItemType Directory -Force -Path $dest | Out-Null

Write-Host ""
Write-Host "正在导出（共 $($paths.Count) 个文件）..." -ForegroundColor DarkGray
foreach ($p in $paths) {
    $name = Split-Path -Path $p -Leaf
    # base.apk 用「包名.apk」命名；split 包带上包名前缀避免重名
    if ($name -eq 'base.apk') { $local = Join-Path $dest "$pkg.apk" }
    else { $local = Join-Path $dest "$pkg-$name" }

    & $adb pull $p $local 2>&1 | Out-Null
    if (Test-Path -LiteralPath $local) {
        $sizeMb = [math]::Round((Get-Item -LiteralPath $local).Length / 1MB, 2)
        Write-Host ("  OK  {0}  ({1} MB)" -f (Split-Path -Path $local -Leaf), $sizeMb) -ForegroundColor Green
    }
    else {
        Write-Host ("  失败 {0}" -f $name) -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "导出完成，文件在：$dest" -ForegroundColor Green
Write-Host "下一步：跑 jadx 反编译看接口。" -ForegroundColor Cyan
Write-Host ""
