# claude-backup

定期备份 Claude 个人账号数据的小工具。思路：账号不可用后就无法再导出，所以平时就要导出，并且至少存两处。

> **本仓库是公开仓库，只放工具代码。** 导出数据、记忆、skill 源文件都放在仓库外的 `BACKUP_ROOT`
> 和单独的**私有**资产仓库里。`.gitignore` 已排除常见数据路径，但不要依赖它，别把数据目录放进本仓库。

## 分层：备份什么、放哪里

| 层级 | 内容 | 源头版本放在哪 | 本工具负责 |
|---|---|---|---|
| 1 自己沉淀的资产 | skill 源文件、Project instructions、项目知识文件、规则库、提示词模板 | 私有 GitHub 仓库（`ASSETS_REPO`）| `sync.sh` 镜像本地 skill 目录、提交并推送 |
| 2 对话中的有效产出 | 定稿剧本、分镜、调试出的规则 | 产出当下另存为文档，归入资产仓库 | `import` 把导出转成可检索的 Markdown 留档 |
| 3 记忆 | Settings 里的记忆内容 | `BACKUP_ROOT/memory/` | `memory` 子命令按日期存档并比对差异 |
| 4 原始导出包 | 官方 zip | `BACKUP_ROOT/exports/raw/` | `import` 原样保留并记录 sha256 |

官方导出里能看到哪些内容，以实际导入时的输出为准：`import` 会列出包里找到的文件，以及无法识别的文件。
如果项目知识文件或记忆没有出现在导出里，说明这一层只能靠你手动维护源头版本。

## 安装

只依赖 Python 3.8+ 和 rsync（macOS 自带）。

```bash
git clone https://github.com/HR-H/Linux.git ~/tools/claude-tools
```

```bash
cd ~/tools/claude-tools/claude-backup && cp config.example.sh config.local.sh
```

编辑 `config.local.sh`，至少设置 `BACKUP_ROOT`，以及一个**不在本机硬盘上**的 `DESTINATIONS`（云盘同步目录或移动硬盘）。

资产仓库（只需做一次）：在 GitHub 新建一个 **Private** 仓库，例如 `claude-assets`，然后：

```bash
git clone https://github.com/<你的用户名>/claude-assets.git ~/claude-assets
```

把各个 skill 的源文件、各项目的 instructions 和知识文件整理进去。jimeng-storyboard-optimizer 已经有自己的仓库，可以用 git submodule 引入，或保持独立。

## 每周流程（约 5 分钟）

1. 网页版或桌面版：**Settings → Privacy → Export data**（手机 App 不能发起导出）。
2. 收到邮件后 **24 小时内**下载（链接需要登录才能打开）。
3. 导入：
   ```bash
   python3 claude_backup.py import ~/Downloads/data-2026-10-31-xx-xx-xx.zip
   ```
   输出会告诉你：新增/更新/**消失**的对话数。“消失”表示对话在账号里被删掉了，旧快照仍然保留。
4. 记忆：到 Settings 里打开记忆页面，全选复制，然后：
   ```bash
   pbpaste | python3 claude_backup.py memory
   ```
5. 同步到多个位置，并推送资产仓库：
   ```bash
   ./sync.sh
   ```
   只要有一个目的地失败、校验不一致，或者一份异地副本都没有，脚本就返回非零。

完成重要工作后，额外跑一遍第 1–5 步。

### 过期提醒（可选）

```bash
python3 claude_backup.py status --max-age 7
```

超过 7 天没有导入，会返回 1 并提示。可以加进 crontab，每周一上午提醒：

```bash
(crontab -l 2>/dev/null; echo '0 10 * * 1 python3 ~/tools/claude-tools/claude-backup/claude_backup.py status || osascript -e "display notification \"该导出 Claude 数据了\" with title \"claude-backup\""') | crontab -
```

## 备份目录结构

```
~/ClaudeBackup/
├── exports/
│   ├── raw/2026-10-31_data-....zip      # 原始导出包，不改动
│   └── 2026-10-31/
│       ├── manifest.json                # 计数、sha256、与上次的差异、对话目录
│       ├── index.md                     # 对话索引（按最近更新排序）
│       ├── conversations/*.md           # 每个对话一个 Markdown
│       ├── projects/<名称>/instructions.md, docs/   # 如果导出中包含项目
│       ├── memory/memory.md             # 如果导出中包含记忆
│       └── users.json
└── memory/2026-10-31.md                 # 手动保存的记忆
```

## 局限（如实说明）

- **下载这一步无法自动化**：导出链接要求登录，脚本拿不到，只能手动点击下载。
- **导出不能导入另一个个人账号**。这里的备份用于自己留档、再喂给新账号或其他工具，不能换号后一键恢复。
- 导出的 JSON 结构是官方内部格式，可能变化。转换器遇到不认识的内容块会原样以 JSON 保留，不会丢弃；
  原始 zip 也始终保留，格式变了可以重新转换。
- 对话里上传的二进制文件（图片、PDF 本体）一般不在导出中，只有文件名或提取出的文本。
- 云盘同步目录本身不算备份校验：`sync.sh` 只能确认文件已写入本地同步文件夹，云端是否上传完成要看云盘客户端。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
