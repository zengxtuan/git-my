#!/usr/bin/env node
/**
 * 给已安装的 dsh-purge 打一个幂等补丁：
 *
 * 起因：新版 @linxin666/dsh-liangshen 的 presets/liangshen/tool-bootstrap.mjs
 * 只收窄「工具目录」(assembled.tools)，不再过滤「system-prompt sections」。
 * 而 dsh-purge 的 phase-1（patch #28 LIANGSHEN_PHASE1_KEEP_INJECT）还在找
 * `assembled.sections` 的过滤代码，找不到就返回 pattern_miss —— 状态显示成
 * 「坏了」，实际上是没有补丁对象。
 *
 * 本补丁让 phase-1 在「所有候选文件都不含 assembled.sections」时返回
 * already（无需处理），而不是 pattern_miss。
 *
 * 用法：
 *   node tools/patch-purge-phase1.mjs [core.js 路径]
 * 默认路径：
 *   D:\deep\harness\profiles\desktop\node_modules\dsh-purge\lib\core.js
 */

import fs from "node:fs";

const DEFAULT_CORE =
  "D:/deep/harness/profiles/desktop/node_modules/dsh-purge/lib/core.js";

const corePath = process.argv[2] || DEFAULT_CORE;

const ANCHOR = [
  "  const files = listLiangshenBootstrapFiles(dshHome);",
  '  if (files.length === 0) return "missing";',
  '  let hit = "missing";',
].join("\n");

const BLOCK = [
  "  // Newer @linxin666/dsh-liangshen bootstraps only narrow the TOOL catalog",
  "  // (assembled.tools) and no longer filter `assembled.sections`. When no",
  "  // candidate carries a sections filter there is nothing for phase-1 to",
  "  // widen, which is the intended end state — report \"already\" rather than",
  "  // pattern_miss so the patch does not look broken.",
  "  const phase1Texts = files",
  "    .map((fp) => {",
  "      try {",
  '        return fs.readFileSync(fp, "utf8");',
  "      } catch {",
  '        return "";',
  "      }",
  "    })",
  "    .filter(Boolean);",
  "  if (phase1Texts.length > 0 && phase1Texts.every((text) => !text.includes(\"assembled.sections\"))) {",
  '    return "already";',
  "  }",
].join("\n");

const MARKER = "phase1Texts.every((text) => !text.includes";

// ---- 第二处改动：状态表对齐 ----
// 通用 marker 匹配同样找不到 #28 的替换点，会把它记成 skipped。
const ANCHOR2 = [
  "export async function patchStatus(aiBase, patches = ALL_PATCHES) {",
  "  const files = targetFiles(aiBase);",
  "  const status = {};",
].join("\n");

const BLOCK2 = [
  "  // phase-1 (#28) 的通用 marker 匹配会命中不到「只有工具目录、没有 sections",
  "  // 过滤」的新版梁神预设；此时 silenceLiangshenPhase1Strip 已判定为 already，",
  "  // 状态表应与之一致，否则 UI 上会长期显示 skipped，看起来像插件坏了。",
  '  let phase1State = "";',
  "  try {",
  "    phase1State = silenceLiangshenPhase1Strip();",
  "  } catch {",
  '    phase1State = "";',
  "  }",
].join("\n");

const GUARD2 = [
  "    if (p.id === 28 && phase1State === \"already\") {",
  "      status[p.id] = \"applied\";",
  "      continue;",
  "    }",
].join("\n");

const MARKER2 = 'if (p.id === 28 && phase1State === "already")';

if (!fs.existsSync(corePath)) {
  console.error(`找不到目标文件: ${corePath}`);
  process.exit(1);
}

let text = fs.readFileSync(corePath, "utf8");
let changed = 0;

// 改动 1：phase-1 判定
if (text.includes(MARKER)) {
  console.log(`[1/2] 已打过，跳过`);
} else if (!text.includes(ANCHOR)) {
  console.error("[1/2] 未找到插入锚点，可能 dsh-purge 版本已变。请手动核对后更新本脚本。");
  process.exit(2);
} else {
  text = text.replace(ANCHOR, `${ANCHOR}\n${BLOCK}`);
  changed += 1;
  console.log("[1/2] phase-1 判定已修复");
}

// 改动 2：状态表对齐
if (text.includes(MARKER2)) {
  console.log(`[2/2] 已打过，跳过`);
} else if (!text.includes(ANCHOR2)) {
  console.error("[2/2] 未找到 patchStatus 锚点，可能 dsh-purge 版本已变。");
  process.exit(2);
} else {
  text = text.replace(ANCHOR2, `${ANCHOR2}\n${BLOCK2}`);
  text = text.replace(
    `${ANCHOR2}\n${BLOCK2}\n    if (!patchAppliesHere(p)) {`,
    `${ANCHOR2}\n${BLOCK2}\n    if (!patchAppliesHere(p)) {`,
  );
  // 把 guard 插到「跳过 na 判定之后」的位置
  text = text.replace(
    `    if (!patchAppliesHere(p)) {\n      status[p.id] = "na";\n      continue;\n    }`,
    `    if (!patchAppliesHere(p)) {\n      status[p.id] = "na";\n      continue;\n    }\n${GUARD2}`,
  );
  if (!text.includes(MARKER2)) {
    console.error("[2/2] guard 插入失败，请手动核对。");
    process.exit(2);
  }
  changed += 1;
  console.log("[2/2] 状态表 #28 已对齐");
}

if (changed === 0) {
  console.log(`无需改动: ${corePath}`);
  process.exit(0);
}

fs.writeFileSync(corePath, text, "utf8");
console.log(`补丁已应用: ${corePath}`);
console.log(`  ${fs.statSync(corePath).size} bytes`);
console.log("重启 DSH 后 phase-1=already，状态表 #28 显示 applied。");
