# xiaoyuzhou-audio

小宇宙播客节目清单与原始音频下载 Skill。适用于 Codex、Claude Code 等支持 Agent Skills、文件操作和 Shell 的客户端。

默认列出频道最近 **15 期**，也支持指定数量或完整频道清单；以表格标明免费和付费，下载用户选定的一集或多集原始音频。

当前版本：**v0.2.0** · Python **3.10+** · **MIT License**

## 功能

| 能力 | 行为 |
|---|---|
| 最近节目 | 未指定数量时返回最新15条 |
| 指定数量 | 用户指定N条，就按发布时间取最新N条 |
| 全部节目 | 合并公开来源、去重并核对频道声明总数；未取全时明确报告 |
| 节目清单 | 默认表格：列表编号、标题、日期与时区、时长、免费／付费、音频可获取状态 |
| 单集下载 | 支持直接提供单集链接，也支持清单编号、单集ID和明确的节目期号 |
| 多集下载 | 支持多个链接、多个编号、连续编号范围和下载最新N期 |
| 付费跳过 | 清单保留付费标题；下载时跳过，连试听或试看音频也不请求 |
| 原始格式 | 保留收到的原始字节和实际格式，不转码、不重封装 |
| 下载目录 | 每位用户首次确认后记住自己的选择，临时目录不会改默认设置 |
| 文件保护 | 不覆盖旧文件，不自动改名，不自动重试；失败清理本次临时文件 |

## 安装

### 使用 skills CLI

需要本机已有 Node.js 和 npm。交互安装时可以选择目标客户端和安装范围：

```bash
npx -y skills add i-YOLO/xiaoyuzhou-audio
```

只为 Codex 全局安装：

```bash
npx -y skills add i-YOLO/xiaoyuzhou-audio --skill xiaoyuzhou-audio --agent codex --global --yes
```

如果明确希望给所有支持的 Agent 全局安装：

```bash
npx -y skills add i-YOLO/xiaoyuzhou-audio -g --all
```

安装后在下一次请求中使用 `$xiaoyuzhou-audio`，或直接提出小宇宙节目清单、音频下载需求。部分客户端可能需要重新加载技能。

### 手动安装

```bash
git clone https://github.com/i-YOLO/xiaoyuzhou-audio.git
```

将仓库中的 `skills/xiaoyuzhou-audio` **整个目录**复制到你的 Agent 支持的技能目录。不要只复制 `SKILL.md`，脚本和操作说明也是运行资源。已有同名 Skill 时先核对版本和位置，避免覆盖正在使用的副本。

Skill 的安装和调用本身不需要 Node.js；只有选择 `npx` 安装方式时才需要。

## 在 Agent 中使用

安装后可以这样说：

```text
使用 $xiaoyuzhou-audio，列出这个频道最近15期：<频道链接>

使用 $xiaoyuzhou-audio，列出这个频道最新30期：<频道链接>

使用 $xiaoyuzhou-audio，列出这个频道的全部节目：<频道链接>

下载刚才清单中的第1、3、5到8项。

下载这个频道的EP88。

下载这个单集的原始音频：<单集链接>

下载这个频道最新5期的免费音频：<频道链接>
```

- 频道链接：`https://www.xiaoyuzhoufm.com/podcast/<24位ID>`。
- 单集链接：`https://www.xiaoyuzhoufm.com/episode/<24位ID>`。
- “列表第14项”和“节目141”是两种编号，Skill 会分别处理；有歧义时先澄清。
- 仅列出清单不会自动下载。已经明确选择要下载的节目时，不再重复询问选择。

## 首次确认下载目录

首次使用时，Skill 会先显示当前系统实际默认目录并询问一次：

> 建议保存到系统下载目录下的“小宇宙”文件夹。使用这个位置，还是另选目录？

| 系统 | 默认下载位置 |
|---|---|
| macOS | 当前用户的 `~/Downloads/小宇宙/` |
| Windows | 当前用户实际系统 Downloads 文件夹中的 `小宇宙` 子目录，支持移动过的下载目录 |

确认后继续原来的请求，并在以后直接沿用。**未回复不等于确认。** 请求中已明确给出长期目录时直接采用；“这次保存到……”只临时覆盖，“以后默认保存到……”才更新默认设置。

配置文件位于 Skill 之外，按用户分别保存：

- macOS：`~/Library/Application Support/xiaoyuzhou-audio/settings.json`。
- Windows：`%APPDATA%/xiaoyuzhou-audio/settings.json`。
- Linux：`$XDG_CONFIG_HOME/xiaoyuzhou-audio/settings.json`，未设置时使用 `~/.config/`。

**仓库不包含个人用户名、个人绝对下载路径、已确认设置或操作快照。** 安装给另一位用户后，会计算那位用户自己的系统默认位置并等待确认。

## Python 命令行

在仓库根目录执行。以下使用 `python3`；Windows 可换为 `py -3`。

```bash
# 本地环境检查，不访问网络
python3 skills/xiaoyuzhou-audio/scripts/podcast.py doctor

# 查看是否已经确认目录；不会自动写入“已确认”
python3 skills/xiaoyuzhou-audio/scripts/podcast.py config show

# 用户确认默认目录后保存
python3 skills/xiaoyuzhou-audio/scripts/podcast.py config set --default

# 用户确认自定义目录后保存
python3 skills/xiaoyuzhou-audio/scripts/podcast.py config set --download-dir "$DOWNLOAD_DIR"

# 默认最近15条 / 指定数量 / 全部
python3 skills/xiaoyuzhou-audio/scripts/podcast.py list "$CHANNEL_URL"
python3 skills/xiaoyuzhou-audio/scripts/podcast.py list "$CHANNEL_URL" --limit 30
python3 skills/xiaoyuzhou-audio/scripts/podcast.py list "$CHANNEL_URL" --all

# 单集或多个明确的单集链接
python3 skills/xiaoyuzhou-audio/scripts/podcast.py download "$EPISODE_URL"
python3 skills/xiaoyuzhou-audio/scripts/podcast.py download "$EPISODE_URL_1" "$EPISODE_URL_2"

# 沿用列出时返回的 catalog_path
python3 skills/xiaoyuzhou-audio/scripts/podcast.py download --catalog "$CATALOG_PATH" --number 2
python3 skills/xiaoyuzhou-audio/scripts/podcast.py download --catalog "$CATALOG_PATH" --numbers '1,3,5-8'
python3 skills/xiaoyuzhou-audio/scripts/podcast.py download --catalog "$CATALOG_PATH" --episodes 'EP88,EP87'

# 仅本次改变下载位置
python3 skills/xiaoyuzhou-audio/scripts/podcast.py download "$EPISODE_URL" --out "$ONE_TIME_DIR"
```

`$CHANNEL_URL`、`$EPISODE_URL`、`$CATALOG_PATH` 等变量需要替换成你自己的链接或路径。内部结果以 JSON 输出到 stdout，下载进度输出到 stderr；Agent 面向用户展示表格和下载汇总。

“下载最新N期”的执行方式是先 `list --limit N`，再对返回的快照执行 `download --catalog ... --all`。这里的 `--all` 只指该快照里的选择，不擅自扩大到整个频道。

完整参数、配置字段及错误处理见 [操作说明](skills/xiaoyuzhou-audio/references/operations.md)。Agent 的工作流程见 [SKILL.md](skills/xiaoyuzhou-audio/SKILL.md)。

## 来源与下载结果

优先读取发布者 RSS，并使用频道公开页面补齐未同步的节目；发布者 RSS 不可用时使用 RSSHub。来源必须核对为同一频道，不根据同名标题混入其他播客。

- 只有返回数量与频道公开总数核对一致，才称为完整频道清单。来源不足时说明“取得多少／总数多少”。
- 收费状态依据平台公开字段判断；RSS 中有音频地址，不代表它是免费节目。
- 下载前重新核对同一单集ID，避免旧快照的收费状态或音频地址已变化。
- 原始格式由实际文件头识别；下载不会改动原音频内容。
- 多集按选择顺序执行，普通单集失败后继续；付费和已存在文件分别记为跳过与已存在。限流、磁盘不足或目录权限错误会停止剩余任务。
- 只有实际写入的新文件记为“下载成功”。长度校验、文件头识别不等于播放器实测。

本 Skill 不做语音转写、AI摘要、订阅、定时任务或付费内容解锁；不请求 Cookie、模型 Key，不上传本地音频或笔记。公共来源可能受网络、服务可用性和页面字段变更影响。

## 开发与测试

运行全部离线测试：

```bash
python3 -B -m unittest discover -s skills/xiaoyuzhou-audio/tests -q
```

当前版本包含 **85 项测试**，覆盖数量规则、完整性、来源归属、首次目录确认、配置持久化、Windows路径逻辑、选集快照、付费跳过、失败继续、限流停止、原始字节一致及旧文件保护。测试数据均为合成数据，不包含真实节目音频或用户私密材料。

本版本已经在 macOS 完成真实频道清单和单集下载／完整解码验收；Windows 路径逻辑通过自动化测试，尚未进行 Windows 实机验收。离线测试通过不代表任意公共实例此刻都在线。

```text
.
├── README.md
├── LICENSE
└── skills/
    └── xiaoyuzhou-audio/
        ├── SKILL.md
        ├── LICENSE
        ├── agents/openai.yaml
        ├── references/operations.md
        ├── scripts/podcast.py
        └── tests/
```

运行时只使用 Python 标准库，不需要安装第三方 Python 包或转码工具。ffmpeg 仅可作为额外的媒体验收工具，并非本 Skill 的依赖。

## 许可证

代码与文档采用 [MIT License](LICENSE)，版权署名为 `2026 i-YOLO`。Skill 目录内也保留了许可证，便于独立安装和分发时保留版权声明。

MIT 许可只覆盖本仓库的代码与文档，不包含播客音频及第三方平台内容的版权授权。本项目不是小宇宙、RSSHub、Apple 或所支持 Agent 客户端的官方产品。
