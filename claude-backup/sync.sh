#!/usr/bin/env bash
# 把备份数据同步到多个目的地，并提交/推送「资产源头仓库」。
# 兼容 macOS 自带 bash 3.2。配置见 config.example.sh（复制为 config.local.sh 后修改）。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${CLAUDE_BACKUP_CONFIG:-$HERE/config.local.sh}"
if [ ! -f "$CONFIG" ]; then
  echo "缺少配置文件: $CONFIG"
  echo "先执行: cp \"$HERE/config.example.sh\" \"$HERE/config.local.sh\" 并按需修改"
  exit 1
fi
# shellcheck source=/dev/null
. "$CONFIG"

: "${BACKUP_ROOT:?config 中未设置 BACKUP_ROOT}"
BACKUP_ROOT="${BACKUP_ROOT/#\~/$HOME}"
status=0

# 1) 资产源头仓库：skills、项目指令、提示词模板等。必须是 *私有* git 仓库。
if [ -n "${ASSETS_REPO:-}" ]; then
  ASSETS_REPO="${ASSETS_REPO/#\~/$HOME}"
  if [ -d "$ASSETS_REPO/.git" ]; then
    # 可选：先把本地 skill 目录镜像进资产仓库
    for src in ${SKILL_SOURCES:-}; do
      src="${src/#\~/$HOME}"
      [ -d "$src" ] || { echo "跳过不存在的 skill 目录: $src"; continue; }
      parent="$(basename "$(dirname "$src")")"
      dest="$ASSETS_REPO/skills/${parent#.}-$(basename "$src")"
      mkdir -p "$dest"
      rsync -a --delete --exclude '.git' "$src/" "$dest/"
    done
    (
      cd "$ASSETS_REPO"
      git add -A
      if git diff --cached --quiet; then
        echo "[assets] 无变化"
      else
        git commit -q -m "backup: $(date +%Y-%m-%d)" && echo "[assets] 已提交"
      fi
      if git remote get-url origin >/dev/null 2>&1; then
        git push -q origin HEAD && echo "[assets] 已推送" || { echo "[assets] 推送失败"; exit 1; }
      else
        echo "[assets] 未配置 remote，只做了本地提交"
      fi
    ) || status=1
  else
    echo "[assets] $ASSETS_REPO 不是 git 仓库，跳过（git init 并关联一个私有远程仓库后再用）"
  fi
fi

# 2) 备份数据同步到每个目的地。只增不删：账号里删掉的对话，备份里仍保留。
[ -d "$BACKUP_ROOT" ] || { echo "备份目录不存在: $BACKUP_ROOT"; exit 1; }
copies=0
for dest in "${DESTINATIONS[@]:-}"; do
  [ -n "$dest" ] || continue
  dest="${dest/#\~/$HOME}"
  if [ ! -d "$(dirname "$dest")" ]; then
    echo "[sync] 目的地不可用（未挂载？）: $dest"
    status=1
    continue
  fi
  mkdir -p "$dest"
  if rsync -a "$BACKUP_ROOT/" "$dest/"; then
    # 校验：用 rsync 校验和模式做一次空跑，若还有差异说明复制不完整
    pending="$(rsync -anc --out-format='%n' "$BACKUP_ROOT/" "$dest/" | grep -v '/$' || true)"
    if [ -z "$pending" ]; then
      echo "[sync] OK → $dest"
      copies=$((copies + 1))
    else
      echo "[sync] 校验不一致 → $dest:"; echo "$pending" | head -20
      status=1
    fi
  else
    echo "[sync] 失败 → $dest"
    status=1
  fi
done

echo "本地原件 1 份 + 同步成功 $copies 份"
if [ "$copies" -lt 1 ]; then
  echo "⚠ 没有任何异地副本，仍是单点故障。"
  status=1
fi
exit $status
