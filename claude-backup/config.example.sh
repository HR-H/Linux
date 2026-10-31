# 复制为 config.local.sh 后修改。config.local.sh 已被 .gitignore 忽略。

# 备份数据目录（claude_backup.py 的输出位置）。不要放在任何公开仓库内。
BACKUP_ROOT="$HOME/ClaudeBackup"

# 同步目的地，至少一个不在本机硬盘上（云盘同步目录 / 移动硬盘）。
# 云盘目录示例（macOS）：
#   iCloud:       "$HOME/Library/Mobile Documents/com~apple~CloudDocs/ClaudeBackup"
#   Google Drive: "$HOME/Library/CloudStorage/GoogleDrive-你的邮箱/My Drive/ClaudeBackup"
#   移动硬盘:     "/Volumes/你的硬盘名/ClaudeBackup"
DESTINATIONS=(
  "$HOME/Library/Mobile Documents/com~apple~CloudDocs/ClaudeBackup"
)

# 资产源头仓库（skill 源文件、Project instructions、知识文件、提示词模板）。
# 必须是私有 GitHub 仓库的本地 clone（即 HR-H/Claudebak）；留空则跳过。
# sync.sh 会先把最近一次导入的快照合并进它的 archive/，再提交推送。
ASSETS_REPO="$HOME/claudebak"

# 可选：要镜像进资产仓库的本地 skill 目录（空格分隔）。
# 会镜像到 $ASSETS_REPO/skills/<上级目录名>-<目录名>/
SKILL_SOURCES="$HOME/.claude/skills"
