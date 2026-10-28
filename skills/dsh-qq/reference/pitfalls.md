# 踩坑清单

按"症状 → 根因 → 修法"组织。每条都是实际遇到过并验证过的。

---

## 1. `.bat` 满屏 `is not recognized as an internal or external command`

**症状**
```
'嚙踝蕭@echo' is not recognized as an internal or external command
'TCH_PACKAGE' is not recognized ...
'Main.exe"' is not recognized ...
```

**根因**：文件带 UTF-8 BOM（`EF BB BF`）。cmd 把 BOM 当成命令的一部分，导致第一行解析失败并连锁。

**修法**：用无 BOM 的 UTF-8 写 `.bat`。

```powershell
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($path, $text, $enc)
```

**验证**：读前 3 字节，不应是 `ef bb bf`。

---

## 2. `.bat` 里变量名被吞掉一半

**症状**：`set "NAPCAT_PATCH_PACKAGE=..."` 那行报 `'TCH_PACKAGE' is not recognized`——开头约 10 个字符消失。多行都呈现"每行开头缺一块"。

**根因**：文件是 LF 换行。cmd 的批处理解析器只认 CRLF，LF 会导致行被错误切分。

**修法**：合成时显式用 CRLF。

```powershell
$text = ($lines -join "`r`n") + "`r`n"
```

**验证**：`CR` 数量应等于 `LF` 数量。

---

## 3. PowerShell 里 `&&` 报 `InvalidEndOfLine`

**症状**
```
标记“&&”不是此版本中的有效语句分隔符。
ParserError: InvalidEndOfLine
```

**根因**：`cd /d ... && xxx` 是 **cmd** 语法。PowerShell 5.1 不支持 `&&`（7+ 才支持）。

**修法**：换成 `;` 或写成两行。

```powershell
cd "D:\path\to\dir"
.\script.bat
```

---

## 4. 配置改了不生效，或者被改回去

**症状**：手工编辑 `data/cmd_config.json` 后重启，改动丢失或部分字段被删除。

**根因**：AstrBot 运行期间持有内存态配置，退出/保存时会把内存态写回磁盘，覆盖磁盘上的手改。

**修法**：**先停进程，再改配置，再启动。** 顺序不能反。

```powershell
Get-Process astrbot | Stop-Process -Force
# 改配置
Start-Process ... astrbot.exe -ArgumentList 'run'
```

另注：字段名要按真实 schema。例如白名单是 `enable_id_white_list`（带下划线），写成 `enable_id_whitelist` 会被当成未知字段清理掉。

---

## 5. NapCat WebUI 的 token 怎么输都 invalid

**症状**：用 `webui.json` 里的 token 登录，接口返回 `{"code":-1,"message":"token is invalid"}`。

**根因**（napcat.mjs 源码）：
```js
try {
  await _0e(t.host);                 // 检查 host
  n = await gm(t.port, t.host);      // 绑定端口
  return [t.host, n, t.token];       // 正常：用配置 token
} catch (e) {
  return ["", 0, cE()];              // 异常：token 换成随机值
}
```
host 绑定那步失败时走进 catch，**内存里的 token 变成随机值**，配置文件里那个根本没被采用。同时表现为"监听在 `::` 而不是配置的 `127.0.0.1`"。

**修法**：让 host 走默认值后重启 NapCat（会掉 QQ 登录，需要重新扫码）。

**旁证**：`Get-NetTCPConnection -LocalPort 6099` 显示 `::` 而不是 `127.0.0.1`，基本可以确认踩到了这个分支。

---

## 6. 语音不发声，但日志里没有明显报错

**症状**：文字正常回复，就是没有语音。日志中出现：
```
TTS request: ...
音频生成失败: [WinError 2] 系统找不到指定的文件。
ModuleNotFoundError: No module named 'pyffmpeg'
TTS failed; sending text instead.
```

**根因**：`edge_tts_source.py` 的流程是 `合成 MP3 → pyffmpeg 转 WAV → 失败则调 ffmpeg 命令行 → 都失败就抛错`。两者都没装就静默回退成文字。

**修法**：装 ffmpeg 并确保在 PATH 里。`pyffmpeg` 这个 Python 封装本身常报 `Error all:`（空错误），但它会把 `ffmpeg.exe` 落到 `%USERPROFILE%\.pyffmpeg\bin\`，可以直接拿来用。

```powershell
[Environment]::SetEnvironmentVariable('Path', $old + ';C:\Users\<u>\.pyffmpeg\bin', 'User')
```

注意：改 PATH 后**必须重启 AstrBot 进程**才继承。

---

## 7. 手机打不开，但本机访问局域网 IP 仍然通

**症状**：加了防火墙 Block 规则后，`curl http://192.168.x.x:6099` 仍返回 200，以为规则没生效。

**根因**：本机访问自己的局域网 IP 时，Windows 走**回环路由**，不经过防火墙的入站链。本机自测无法验证入站规则。

**修法**：用另一台设备（手机、另一台电脑）验证。

---

## 8. GitHub 下载极慢或中途卡死

**症状**：`github.com` 直连 `000` 超时；代理镜像单连接约 87 KB/s 且会"假死"（连接在、数据不来）。

**根因**：直连被阻断；公共镜像共享带宽。

**修法**
- 分段并发下载：切 2MB 分片、多线程、多镜像轮转、单片失败换镜像重试，最后校验总字节数和 zip CRC。
- **不要用断点续传（`curl -C -`）拼接镜像分片**：不同边缘节点的内容可能不一致，拼出来 CRC 必错，整包作废。
- Windows 上 curl 还常撞 `schannel: CRYPT_E_NO_REVOCATION_CHECK`，加 `--ssl-no-revoke` 可解，那其实是"证书吊销检查失败"而非"连不上"。

---

## 9. 收紧 ACL 把文件锁死了

**症状**：`icacls <dir> /inheritance:r /grant:r ...` 之后，目录能进能写，但**目录里的文件全部读不了、删不掉**（`EPERM` / 访问被拒绝）。

**根因**：`/inheritance:r` 移除继承后，若 `/grant:r` 没有覆盖到子文件，文件就成了"无 ACE"状态；连 ACL 本身都读不出来。

**修法**：`takeown` 拿回所有权 + `icacls /reset` 恢复继承，再重新授权，并确保 `/T` 递归生效。

```powershell
takeown /F <dir> /R /A /D Y
icacls <dir> /reset /T /C
icacls <dir> /inheritance:r /grant:r "<user>:(OI)(CI)F" /grant:r "SYSTEM:(OI)(CI)F" /T /C
```

**教训**：动 ACL 前先备份可读的副本，别把唯一凭据文件锁在里面。

---

## 10. 机器人自称"运行在 AstrBot 上的 AI 助手"

**症状**：自我介绍里列出一堆工具（Word/Excel/PDF/Skill/定时任务），还说"需要打开 Computer Use 权限"。

**根因**：没有自定义人格，用的是默认 `system_prompt`，同时 AstrBot 把**工具清单**注入给了模型，模型照着念。

**修法**：在 `personas` 表建人格，明确身份并**显式禁止**复述系统提示/工具清单/客服腔。只换模型不改人格没用——身份由 system prompt 决定。

---

## 11. 长期记忆"没生效"

**症状**：聊了两句就发 `/lmem status`，发现没记住东西。

**根因**：记忆是**攒够对话轮次后批量整理**的，不是逐条写入。

**修法**：聊十几轮有信息的对话（"我下周面试""我爱吃辣"），再查状态。这是设计如此。

---

## 12. 计划任务注册不上

**症状**：`Register-ScheduledTask` 在非交互会话里静默失败，任务列表里查不到。

**根因**：`-Verb RunAs` 在无桌面交互的会话里弹不出 UAC，提权进程根本没起来。

**修法**：改用**启动文件夹 + 常驻守护**，完全不需要管理员：
- `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\xxx.vbs` → `WScript.Shell.Run(..., 0, False)` 静默启动
- 守护脚本 `while($true){ ensure; Start-Sleep 300 }`

---

## 13. 在 PowerShell 里内联跑多行 Python 会炸

**症状**：`python -c "...多行代码..."` 报 `SyntaxError`，引号被吃掉、括号不闭合。

**根因**：PowerShell 对传给 `-c` 的字符串做引号解析，双引号会被剥离。

**修法**：**一律写成 `.py` 文件再执行**。不要用 `-c` 传含引号的多行代码，也不要用 here-string 拼。

---

## 14. 重跑配置包导入会覆盖后台改动

**症状**：在后台改好的人格/插件参数，某次重跑导入脚本后全部回退。

**根因**：导入工具按包内 `settings/pack.json` 覆盖写入。

**修法**：定死一条规则——**要么只在后台改，要么只改包再导入**，不要混用。
