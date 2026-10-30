# 给 dsh-purge 打「去 {{cwd}}」补丁（幂等）。
#
# 背景：dsh 0.2.x 的 system-prompt 只注册 provider / model / vision_router_mode_sync，
# 而 dsh-purge 在运行时把 persona-suffix 改写成 "Your working directory is {{cwd}}."，
# 导致每一轮报 unknown prompt variable "{{cwd}}" 并失败。静态 cordis.patch.yml 覆盖无效，
# 因为改写发生在插件运行时的 identity overlay 里。
#
# pnpm 重装插件后需要重新执行本脚本。
param(
  [string]$ProfileDir = (Join-Path $HOME '.dsh\profiles\web')
)

$ErrorActionPreference = 'Stop'
$target = Join-Path $ProfileDir 'node_modules\dsh-purge\lib\identity.js'
if (-not (Test-Path $target)) { throw "找不到 $target（先在目标 profile 里装好 dsh-purge）" }

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$text = [System.IO.File]::ReadAllText($target)
$before = $text
$steps = New-Object System.Collections.Generic.List[string]

# 1) 常量本体替换
$oldSuffix = 'export const CWD_ONLY_SUFFIX = "Your working directory is {{cwd}}.";'
$newSuffix = 'export const CWD_ONLY_SUFFIX = "Your working directory is defined by the session workspace.";'
if ($text.Contains($oldSuffix)) { $text = $text.Replace($oldSuffix, $newSuffix); $steps.Add('CWD_ONLY_SUFFIX') }

# 2) 补一个安全常量声明（只补一次）
if (-not $text.Contains('SAFE_WORKSPACE_SUFFIX =')) {
  $anchor = 'export const CWD_ONLY_SUFFIX = "Your working directory is defined by the session workspace.";'
  $decl = $anchor + "`nexport const SAFE_WORKSPACE_SUFFIX = `"Working directory: the session workspace.`";"
  if ($text.Contains($anchor)) { $text = $text.Replace($anchor, $decl); $steps.Add('SAFE_WORKSPACE_SUFFIX 声明') }
}

# 3) 让 cwd-only 判定同时认识安全常量和老文本
$oldCheck = @'
  return t === CWD_ONLY_SUFFIX
    || t === "Your working directory is {{cwd}}."
'@
$newCheck = @'
  return t === CWD_ONLY_SUFFIX
    || t === SAFE_WORKSPACE_SUFFIX
    || t === "Your working directory is {{cwd}}."
'@
if ($text.Contains($oldCheck)) {
  $text = $text.Replace($oldCheck, $newCheck)
  $steps.Add('isCwdOnlyText')
} elseif (-not $text.Contains('t === SAFE_WORKSPACE_SUFFIX')) {
  # 旧版 dsh-purge 的 isCwdOnlyText 只有 Working directory 两种形态，没有
  # "Your working directory is ..." 那一行：直接在首行后插入两条判定。
  $rx = New-Object System.Text.RegularExpressions.Regex('(?m)^([ \t]*)return t === CWD_ONLY_SUFFIX\r?\n')
  $text = $rx.Replace($text, {
      param($m)
      $ind = $m.Groups[1].Value
      return $m.Value + $ind + '|| t === SAFE_WORKSPACE_SUFFIX' + "`n" + $ind + '|| t === "Your working directory is {{cwd}}."' + "`n"
    }, 1)
  $steps.Add('isCwdOnlyText（旧版形态）')
}

# 4) persona 清洗的两处替换值不再写 {{cwd}}
$oldValue = '"Working directory: {{cwd}}. "'
$newValue = 'SAFE_WORKSPACE_SUFFIX + " "'
if ($text.Contains($oldValue)) { $text = $text.Replace($oldValue, $newValue); $steps.Add('stripHarnessPersona 替换值') }

if ($text -ne $before) {
  [System.IO.File]::WriteAllText($target, $text, $utf8NoBom)
  Write-Output ("已改写 {0}" -f $target)
  $steps | ForEach-Object { "  - $_" }
} else {
  Write-Output "无需改动：$target 已是补丁后状态"
}

# 校验
$bad = Select-String -Path $target -Pattern 'CWD_ONLY_SUFFIX = "Your working directory is \{\{cwd\}\}\."|"Working directory: \{\{cwd\}\}\. "'
$dup = (Select-String -Path $target -Pattern 'SAFE_WORKSPACE_SUFFIX = ').Count
if ($bad) {
  Write-Output '警告：仍有注入点未清除：'
  $bad | ForEach-Object { "  line $($_.LineNumber): $($_.Line.Trim())" }
  exit 1
}
if ($dup -gt 1) {
  Write-Output "警告：SAFE_WORKSPACE_SUFFIX 声明重复 $dup 次，会导致 duplicate export"
  exit 1
}
Write-Output "校验通过：无 {{cwd}} 注入文本，常量声明 $dup 份"
