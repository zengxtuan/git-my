// 直接给 dsh-purge 打「去 {{cwd}}」补丁（node 实现，绕开 PowerShell 执行策略）。
// 用法: node patch-purge-cwd.mjs <profileDir>
import fs from 'node:fs';
import path from 'node:path';

const profileDir = process.argv[2];
if (!profileDir) {
  console.error('usage: node patch-purge-cwd.mjs <profileDir>');
  process.exit(2);
}
const target = path.join(profileDir, 'node_modules', 'dsh-purge', 'lib', 'identity.js');
if (!fs.existsSync(target)) {
  console.error('missing ' + target);
  process.exit(2);
}

let text = fs.readFileSync(target, 'utf8');
const before = text;
const steps = [];

// 1) 常量本体
const oldSuffix = 'export const CWD_ONLY_SUFFIX = "Your working directory is {{cwd}}.";';
const newSuffix = 'export const CWD_ONLY_SUFFIX = "Your working directory is defined by the session workspace.";';
if (text.includes(oldSuffix)) { text = text.replace(oldSuffix, newSuffix); steps.push('CWD_ONLY_SUFFIX'); }

// 2) 安全常量声明（只补一次）
if (!text.includes('SAFE_WORKSPACE_SUFFIX =')) {
  const anchor = 'export const CWD_ONLY_SUFFIX = "Your working directory is defined by the session workspace.";';
  if (text.includes(anchor)) {
    text = text.replace(anchor, anchor + '\nexport const SAFE_WORKSPACE_SUFFIX = "Working directory: the session workspace.";');
    steps.push('SAFE_WORKSPACE_SUFFIX');
  }
}

// 3) isCwdOnlyText 判定补全
if (!text.includes('t === SAFE_WORKSPACE_SUFFIX')) {
  const rx = /^([ \t]*)return t === CWD_ONLY_SUFFIX\r?\n/m;
  if (rx.test(text)) {
    text = text.replace(rx, (m, ind) =>
      m + ind + '|| t === SAFE_WORKSPACE_SUFFIX\n' +
      ind + '|| t === "Your working directory is {{cwd}}."\n');
    steps.push('isCwdOnlyText');
  }
}

// 4) stripHarnessPersona 两处替换值
const oldValue = '"Working directory: {{cwd}}. "';
if (text.includes(oldValue)) { text = text.replaceAll(oldValue, 'SAFE_WORKSPACE_SUFFIX + " "'); steps.push('stripHarnessPersona'); }

if (text !== before) {
  fs.writeFileSync(target, text, 'utf8');
  console.log('patched ' + target);
  for (const s of steps) console.log('  - ' + s);
} else {
  console.log('no change needed: ' + target);
}

// 校验
const leftovers = text.match(/CWD_ONLY_SUFFIX = "Your working directory is \{\{cwd\}\}\."|"Working directory: \{\{cwd\}\}\. "/g) || [];
const declCount = (text.match(/SAFE_WORKSPACE_SUFFIX = /g) || []).length;
console.log(`leftover-injections=${leftovers.length} safe-declarations=${declCount}`);
process.exit(leftovers.length === 0 && declCount === 1 ? 0 : 1);
