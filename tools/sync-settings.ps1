# 收尾：无 BOM 重写 profiles/web/package.json，并按顶层键合并 settings.yaml
$ErrorActionPreference = 'Stop'
$src = Join-Path $env:APPDATA 'dsh-desktop\harness'
$dst = Join-Path $HOME '.dsh'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

Write-Output "[1/2] rewrite package.json without BOM"
$pkgPath = Join-Path $dst 'profiles\web\package.json'
$raw = [System.IO.File]::ReadAllText($pkgPath)
$pkg = $raw | ConvertFrom-Json
$txt = $pkg | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($pkgPath, $txt, $utf8NoBom)
Write-Output ("      bytes=" + (Get-Item $pkgPath).Length + " bom=" + ($(Get-Content $pkgPath -Encoding byte -TotalCount 3) -join ','))

Write-Output "[2/2] merge settings.yaml by top-level key"
function Read-TopLevel($path) {
  $order = New-Object System.Collections.Generic.List[string]
  $blocks = @{}
  $cur = $null
  foreach ($line in [System.IO.File]::ReadAllLines($path)) {
    if ($line -match '^([A-Za-z0-9_.\-]+):') {
      $cur = $matches[1]
      if (-not $blocks.ContainsKey($cur)) { $order.Add($cur) }
      $blocks[$cur] = New-Object System.Collections.Generic.List[string]
      $blocks[$cur].Add($line)
    } elseif ($cur -ne $null) {
      $blocks[$cur].Add($line)
    }
  }
  return @{ order = $order; blocks = $blocks }
}

$srcSet = Read-TopLevel (Join-Path $src 'settings.yaml')
$dstSet = Read-TopLevel (Join-Path $dst 'settings.yaml')

$order = New-Object System.Collections.Generic.List[string]
foreach ($k in $srcSet.order) { $order.Add($k) }
foreach ($k in $dstSet.order) { if (-not $order.Contains($k)) { $order.Add($k) } }

$out = New-Object System.Collections.Generic.List[string]
foreach ($k in $order) {
  if ($srcSet.blocks.ContainsKey($k)) { $out.AddRange($srcSet.blocks[$k]) }
  elseif ($dstSet.blocks.ContainsKey($k)) { $out.AddRange($dstSet.blocks[$k]) }
}
$settingsPath = Join-Path $dst 'settings.yaml'
[System.IO.File]::WriteAllText($settingsPath, ($out -join "`n") + "`n", $utf8NoBom)
Write-Output ("      keys: " + ($order -join ', '))
Write-Output "----- merged settings.yaml -----"
Get-Content $settingsPath
