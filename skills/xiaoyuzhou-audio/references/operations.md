# 运行参考（v0.3.0：清单与下载）

## 命令与结果

脚本仅依赖 Python 3.10+ 标准库。正常结果和业务错误输出 JSON 到 stdout，进度输出到 stderr；参数错误由 argparse 输出。`ok=false` 返回非零退出码，但可能包含有效的部分清单或多集汇总，先读 `status`、`error` 与各项目结果。

```bash
python3 scripts/podcast.py doctor
python3 scripts/podcast.py config show
python3 scripts/podcast.py config set --default
python3 scripts/podcast.py config set --download-dir "$CONFIRMED_DIR"
python3 scripts/podcast.py list "$CHANNEL_URL"                    # 默认15条
python3 scripts/podcast.py list "$CHANNEL_URL" --limit 30
python3 scripts/podcast.py list "$CHANNEL_URL" --all
python3 scripts/podcast.py list "$CHANNEL_URL" --catalog "$NEW_SNAPSHOT"
python3 scripts/podcast.py download "$EPISODE_URL"
python3 scripts/podcast.py download "$EPISODE_URL_1" "$EPISODE_URL_2"
python3 scripts/podcast.py download --catalog "$CATALOG_PATH" --numbers '1,3,5-8'
python3 scripts/podcast.py download --catalog "$CATALOG_PATH" --episodes '141'
python3 scripts/podcast.py download --catalog "$CATALOG_PATH" --id "$STABLE_ID"
python3 scripts/podcast.py download --catalog "$CATALOG_PATH" --ids "$ID_1,$ID_2"
python3 scripts/podcast.py download --catalog "$CATALOG_PATH" --all
```

`--number/--numbers` 是当前列表编号；`--episodes` 是标题开头的节目期号，支持 `EP88`、`E47`、`141`、`3#`。多个同号标题会报 `ambiguous_episode`，不挑第一条。无期号的节目用稳定 ID 或链接。选集方式互斥；无效编号会在任何音频下载前报错。只给频道链接不会自动下载。

下载参数：`--out` 临时覆盖目录；`--name` 是用户明确指定的单集新文件名（不含扩展名）；`--timeout` 默认每次网络操作30秒；`--deadline` 默认每集下载总时长1800秒；`--max-mb` 默认每集2048 MiB。没有自动改名、覆盖、重试、断点续传或格式转换。

## 用户配置与目录

- macOS：`~/Library/Application Support/xiaoyuzhou-audio/settings.json`。
- Windows：`%APPDATA%/xiaoyuzhou-audio/settings.json`。
- Linux：`$XDG_CONFIG_HOME/xiaoyuzhou-audio/settings.json`，未设置时为 `~/.config/...`。
- `XIAOYUZHOU_AUDIO_CONFIG` 可指定独立配置文件，主要用于隔离测试；不修改系统 HOME。

初始状态 `directory_confirmed=false`，`config show` 不创建配置、不代表用户确认。用户明确选择后运行 `config set`；它原子更新本 Skill 自己的设置并保留其他已有字段。`download` 没有 `--out` 且目录尚未确认时返回 `directory_confirmation_required`。显式 `--out` 只影响当次下载，不改持久设置。配置损坏用用户明确指定的 `config set` 修复。

默认目录为系统 Downloads 下的 `小宇宙`。Windows 查询 User Shell Folders 的 Downloads 位置，未能查询时使用用户主页的 Downloads。配置与操作快照在 Skill 外，升级保留配置，分享源包不包含这些数据。快照默认保存到配置目录的 `catalogs/`；旧版 schema_version=1 的快照仍可读。

## 来源、完整性与收费状态

1. 频道公开页提供频道身份、声明总数、近期节目及收费字段。
2. 通过 Apple 公开播客目录发现发布者 RSS；根据频道链接或与频道公开节目的单集 ID 交集核对归属，不混入同名播客。
3. 发布者 RSS 与公开页按单集 ID 合并、按发布日期降序排列。发布者 RSS 不可用时，使用 RSSHub；用户也可通过 `--feed` 提供公开发布者 RSS，或 `--rsshub` 指定实例。

返回 `expected_count`（频道声明总数）、`collected_count`（去重取得数）、`selected_count`（展示数）、`requested_count`、`request_satisfied`、`complete_history`。`history_verified` 表示来源已核对完整，而 `complete_history` 仅在返回快照本身包含所有节目时才为 true，默认15条不会冒充完整列表。只有声明总数已知且返回数量相等才能核实全部；`--all` 不能核实、或指定数量不足时返回 `incomplete_catalog` 和实际部分结果。频道总数本来较小时，请求更多条只返回实际总数。

收费状态优先读公开单集页 `payType` 与 `isPrivateMedia`。已定位的付费身份与频道声明付费数量完全相符时，其他条目可标免费，并注明内部核对来源；未能核实则保留 `access=unknown`，表格显示“待核实”。`restricted` 表示受限，不伪称付费或公开可获取。付费及受限条目不在快照中保存可下载音频地址。

`available` 表示来源提供了音频附件地址，不等于已经下载或播放器验证。每次真正下载前都会重新核对同一单集公开页面；旧快照曾为免费但现在付费时也跳过。单集标题变更采用本次公开页标题，编号与单集 ID 不变，旧快照不被覆写。

RSS 最大8 MiB，gzip 解压读取也有上限；JSON快照最大32 MiB。超过上限报错，不将截断内容当成完整。URL限制协议、嵌入凭据、部分私网主机与重定向；未做 DNS 绑定，这是本地工具，不作为公开安全下载代理部署。

## 多集状态与错误

按用户选择顺序去重、逐集执行。`downloaded` 才表示本次成功发布了新文件；`skipped_paid` 不会请求音频；`exists` 保留原文件；`failed` 普通错误后继续；`not_attempted` 是整批停止后的剩余项目。

批次返回 `requested_count`、`completed_count`、`skipped_paid_count`、`existing_count`、`failed_count`、`not_attempted_count` 和逐项 `results`。全部都是付费或已存在时也可能完成了处理，但不能声称下载了文件。

| 错误 | 行为 |
|---|---|
| `incomplete_catalog` | 展示实际数量，明确未拿全；不直接称为全部 |
| `paid_content` | 跳过这一集，不请求音频，继续其他免费选择 |
| `public_audio_unverified` | 单集失败，保留原因，继续其他选择 |
| `file_exists` | 原文件保留，记已存在；用户指定新名字后才重下 |
| `network_error` / `http_404` / `http_401` / `http_403` | 此集失败，不绕过，继续其他明确选择 |
| `http_429` | 停止本批，剩余项目未执行，不自动重试 |
| 磁盘不足、只读、目录无权限 | 停止本批，保留已成功文件和错误原因 |
| `incomplete_download` / `not_audio` / `unknown_audio` | 不发布损坏或伪装文件，清理本次临时文件 |
| 用户中止 | 清理当前临时文件，保留成功项，剩余项目未执行 |

## 验证与维护入口

```bash
python3 -B -m unittest discover -s tests -q
```

自动化测试用合成 RSS、公开页与音频验证行为；真实频道核对和实际下载/解码验收另外记录，不能用离线通过替代。运行时不依赖 ffmpeg；验收可独立用 ffprobe/ffmpeg 检查实际媒体。

来源核对日期：2026-10-04。

- RSSHub 路由：[官方源码](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/xiaoyuzhou/podcast.ts)。
- 发布者 RSS 发现：[Apple Search API](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/Searching.html)。
- 总数和收费字段来自用户提供链接所对应的小宇宙公开页面；访问受限或字段变更时报告未能核实。

本工具不是小宇宙、RSSHub 或 Apple 的官方产品。
