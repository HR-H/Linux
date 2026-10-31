#!/usr/bin/env python3
"""Claude 数据备份工具（仅依赖 Python 3.8+ 标准库）。

子命令:
  import <export.zip|目录>   导入官方导出包：保留原始 zip，转换为可读 Markdown，生成清单并与上次对比
  status [--max-age N]        查看最近一次备份；超过 N 天（默认 7）返回非零退出码，可用于定时提醒
  memory [--from FILE]        保存从 Settings 复制出的记忆文本（默认从标准输入读取），并与上一版对比
  publish [仓库路径]          把最近快照按对话 UUID 累积合并进私有备份仓库（默认 ~/claudebak），不提交

数据目录由 --root 或环境变量 CLAUDE_BACKUP_ROOT 指定，默认 ~/ClaudeBackup。
数据目录不应放在公开仓库里。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

KNOWN_FILES = ("conversations.json", "projects.json", "users.json", "memories.json")
DEFAULT_ROOT = Path(os.environ.get("CLAUDE_BACKUP_ROOT", "~/ClaudeBackup")).expanduser()
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')


# ---------------------------------------------------------------- helpers

def slugify(text: Optional[str], limit: int = 60) -> str:
    text = _UNSAFE.sub("_", (text or "").strip())
    text = re.sub(r"\s+", "-", text).strip("-._")
    return text[:limit].rstrip("-._") or "untitled"


def short_date(value: Optional[str]) -> str:
    return (value or "")[:10] or "0000-00-00"


def pretty_time(value: Optional[str]) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:19]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fence(body: str, lang: str = "") -> str:
    """生成不会被内容中的反引号提前闭合的代码块。"""
    longest = max((len(m) for m in re.findall(r"`+", body)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{body.rstrip()}\n{ticks}"


def details(summary: str, body: str) -> str:
    return f"<details><summary>{summary}</summary>\n\n{body}\n\n</details>"


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


# ---------------------------------------------------------------- loading

def load_export(source: Path) -> Dict[str, Any]:
    """读取导出包中的已知 JSON 文件。zip 只按文件名读取，不整体解压（避免路径穿越）。"""
    found: Dict[str, Any] = {}
    if source.is_dir():
        for name in KNOWN_FILES:
            hits = sorted(source.rglob(name))
            if hits:
                found[name] = json.loads(hits[0].read_text(encoding="utf-8"))
        others = [p.name for p in source.rglob("*.json") if p.name not in KNOWN_FILES]
    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
            for name in KNOWN_FILES:
                hits = sorted(m for m in members if m.rsplit("/", 1)[-1] == name)
                if hits:
                    with zf.open(hits[0]) as fh:
                        found[name] = json.loads(fh.read().decode("utf-8"))
            others = [m for m in members if m.rsplit("/", 1)[-1] not in KNOWN_FILES]
    else:
        raise SystemExit(f"无法识别的导出源（需要 .zip 或解压后的目录）: {source}")
    if "conversations.json" not in found:
        raise SystemExit("导出包中没有 conversations.json，可能不是 Claude 的数据导出。")
    found["_unrecognized"] = sorted(others)
    return found


# ---------------------------------------------------------------- rendering

def render_block(block: Dict[str, Any]) -> str:
    kind = block.get("type")
    if kind == "text":
        return (block.get("text") or "").rstrip()
    if kind == "thinking":
        return details("思考过程", block.get("thinking") or block.get("text") or "")
    if kind == "tool_use":
        payload = json.dumps(block.get("input", {}), ensure_ascii=False, indent=2)
        return details(f"工具调用：{block.get('name', '?')}", fence(payload, "json"))
    if kind == "tool_result":
        inner = block.get("content")
        if isinstance(inner, list):
            body = "\n\n".join(render_block(b) for b in inner if isinstance(b, dict))
        else:
            body = fence(str(inner or ""))
        return details("工具结果", body)
    # 未知块类型：原样保留，避免丢信息
    return details(f"未识别内容块：{kind}", fence(json.dumps(block, ensure_ascii=False, indent=2), "json"))


def render_message(msg: Dict[str, Any]) -> str:
    sender = msg.get("sender") or msg.get("role") or "?"
    who = {"human": "用户", "user": "用户", "assistant": "Claude"}.get(sender, sender)
    parts = [f"### {who} · {pretty_time(msg.get('created_at'))}"]

    blocks = msg.get("content")
    if isinstance(blocks, list) and blocks:
        rendered = [render_block(b) for b in blocks if isinstance(b, dict)]
        parts.append("\n\n".join(r for r in rendered if r))
    elif msg.get("text"):
        parts.append(msg["text"].rstrip())

    for att in msg.get("attachments") or []:
        name = att.get("file_name") or "附件"
        extracted = att.get("extracted_content")
        if extracted:
            parts.append(details(f"附件：{name}", fence(extracted)))
        else:
            parts.append(f"> 附件：{name}（导出中无正文）")
    for f in msg.get("files") or []:
        parts.append(f"> 文件：{f.get('file_name') or f.get('file_uuid') or '?'}（导出不含文件本体）")
    return "\n\n".join(parts)


def render_conversation(conv: Dict[str, Any]) -> str:
    title = conv.get("name") or "（无标题）"
    head = [
        f"# {title}",
        "",
        f"- UUID: `{conv.get('uuid', '')}`",
        f"- 创建: {pretty_time(conv.get('created_at'))}",
        f"- 更新: {pretty_time(conv.get('updated_at'))}",
    ]
    if conv.get("project_uuid") or (conv.get("project") or {}).get("uuid"):
        head.append(f"- 所属项目: `{conv.get('project_uuid') or conv['project']['uuid']}`")
    messages = conv.get("chat_messages") or []
    head.append(f"- 消息数: {len(messages)}")
    body = [render_message(m) for m in messages if isinstance(m, dict)]
    return "\n".join(head) + "\n\n---\n\n" + "\n\n---\n\n".join(body) + "\n"


# ---------------------------------------------------------------- import

def write_conversations(convs: List[Dict[str, Any]], out: Path) -> Dict[str, Dict[str, str]]:
    out.mkdir(parents=True, exist_ok=True)
    index_rows, catalog = [], {}
    ordered = sorted(convs, key=lambda c: c.get("updated_at") or "", reverse=True)
    for conv in ordered:
        uid = str(conv.get("uuid") or "")
        stem = f"{short_date(conv.get('created_at'))}_{slugify(conv.get('name'))}_{uid[:8] or 'nouuid'}"
        path = unique_path(out, stem, ".md")
        path.write_text(render_conversation(conv), encoding="utf-8")
        catalog[uid or path.stem] = {
            "name": conv.get("name") or "",
            "updated_at": conv.get("updated_at") or "",
            "messages": str(len(conv.get("chat_messages") or [])),
            "file": f"conversations/{path.name}",
        }
        index_rows.append(
            f"| {pretty_time(conv.get('updated_at'))} | [{(conv.get('name') or '（无标题）').replace('|', '/')}]"
            f"(conversations/{path.name}) | {len(conv.get('chat_messages') or [])} |"
        )
    (out.parent / "index.md").write_text(
        "# 对话索引（按最近更新排序）\n\n| 更新时间 | 标题 | 消息数 |\n|---|---|---|\n"
        + "\n".join(index_rows) + "\n",
        encoding="utf-8",
    )
    return catalog


def write_projects(projects: List[Dict[str, Any]], out: Path) -> int:
    count = 0
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        pdir = unique_path(out, f"{slugify(proj.get('name'))}_{str(proj.get('uuid', ''))[:8]}", "")
        (pdir / "docs").mkdir(parents=True)
        meta = {k: v for k, v in proj.items() if k != "docs"}
        (pdir / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (pdir / "instructions.md").write_text(
            f"# {proj.get('name') or '（无名项目）'}\n\n{proj.get('description') or ''}\n\n"
            f"## Project instructions\n\n{proj.get('prompt_template') or '（导出中为空）'}\n",
            encoding="utf-8",
        )
        for doc in proj.get("docs") or []:
            name = slugify(doc.get("filename") or doc.get("uuid"), limit=120)
            target = unique_path(pdir / "docs", Path(name).stem, Path(name).suffix or ".txt")
            target.write_text(doc.get("content") or "", encoding="utf-8")
        count += 1
    return count


def write_memories(memories: Any, out: Path) -> bool:
    items = memories if isinstance(memories, list) else [memories]
    lines: List[str] = ["# 导出中的记忆内容", ""]
    for item in items:
        if not isinstance(item, dict):
            lines.append(fence(json.dumps(item, ensure_ascii=False, indent=2), "json"))
            continue
        if item.get("conversations_memory"):
            lines += ["## 全局记忆", "", str(item["conversations_memory"]).rstrip(), ""]
        for puid, text in (item.get("project_memories") or {}).items():
            lines += [f"## 项目记忆 `{puid}`", "", str(text).rstrip(), ""]
        rest = {k: v for k, v in item.items() if k not in ("conversations_memory", "project_memories")}
        if rest:
            lines += ["## 其他字段", "", fence(json.dumps(rest, ensure_ascii=False, indent=2), "json"), ""]
    out.mkdir(parents=True, exist_ok=True)
    (out / "memory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def latest_manifest(root: Path, exclude: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    snaps = sorted(p for p in (root / "exports").glob("*/manifest.json") if p.parent != exclude)
    if not snaps:
        return None
    return json.loads(snaps[-1].read_text(encoding="utf-8"))


def compare(prev: Optional[Dict[str, Any]], current: Dict[str, Dict[str, str]]) -> Dict[str, List[str]]:
    if not prev:
        return {"new": sorted(current), "updated": [], "missing": []}
    old = prev.get("conversations", {})
    return {
        "new": sorted(k for k in current if k not in old),
        "updated": sorted(k for k in current if k in old and current[k]["updated_at"] != old[k].get("updated_at")),
        "missing": sorted(k for k in old if k not in current),
    }


def cmd_import(args: argparse.Namespace) -> int:
    root: Path = args.root
    source = Path(args.source).expanduser().resolve()
    data = load_export(source)

    today = _dt.date.today().isoformat()
    snap = unique_path(root / "exports", today, "")
    snap.mkdir(parents=True)

    raw_dir = root / "exports" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_record = None
    if source.is_file():
        raw_target = raw_dir / f"{snap.name}_{source.name}"
        shutil.copy2(source, raw_target)
        raw_record = {"file": f"exports/raw/{raw_target.name}", "sha256": sha256_of(raw_target),
                      "bytes": raw_target.stat().st_size}

    convs = [c for c in data["conversations.json"] if isinstance(c, dict)]
    catalog = write_conversations(convs, snap / "conversations")
    n_projects = write_projects(data.get("projects.json") or [], snap / "projects") if "projects.json" in data else 0
    has_memory = write_memories(data["memories.json"], snap / "memory") if "memories.json" in data else False
    if "users.json" in data:
        (snap / "users.json").write_text(json.dumps(data["users.json"], ensure_ascii=False, indent=2), encoding="utf-8")

    diff = compare(latest_manifest(root, exclude=snap), catalog)
    manifest = {
        "snapshot": snap.name,
        "imported_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "source": str(source),
        "raw": raw_record,
        "files_found": sorted(k for k in data if not k.startswith("_")),
        "unrecognized_files": data["_unrecognized"],
        "counts": {"conversations": len(catalog), "messages": sum(int(v["messages"]) for v in catalog.values()),
                   "projects": n_projects, "memory": has_memory},
        "diff_vs_previous": {k: len(v) for k, v in diff.items()},
        "conversations": catalog,
    }
    (snap / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    c = manifest["counts"]
    print(f"已导入 → {snap}")
    print(f"  对话 {c['conversations']} 个 / 消息 {c['messages']} 条 / 项目 {c['projects']} 个 / "
          f"记忆 {'有' if has_memory else '导出中未包含（请用 memory 子命令手动保存）'}")
    print(f"  相比上次: 新增 {len(diff['new'])}，更新 {len(diff['updated'])}，消失 {len(diff['missing'])}")
    if diff["missing"]:
        print("  注意：以下对话在本次导出中已不存在（旧快照仍保留）：")
        prev = latest_manifest(root, exclude=snap) or {}
        for uid in diff["missing"][:20]:
            print(f"    - {prev.get('conversations', {}).get(uid, {}).get('name', uid)}")
    if data["_unrecognized"]:
        print(f"  导出包中还有未处理的文件（已随原始 zip 保留）: {', '.join(data['_unrecognized'][:10])}")
    return 0


# ---------------------------------------------------------------- status / memory

def cmd_status(args: argparse.Namespace) -> int:
    root: Path = args.root
    manifest = latest_manifest(root)
    mem_files = sorted((root / "memory").glob("*.md"))
    if not manifest:
        print(f"{root} 下还没有任何导出快照。")
        return 1
    imported = _dt.datetime.fromisoformat(manifest["imported_at"])
    age = (_dt.datetime.now() - imported).days
    c = manifest["counts"]
    print(f"最近快照: {manifest['snapshot']}（{age} 天前）  对话 {c['conversations']} / 项目 {c['projects']}")
    print(f"最近记忆存档: {mem_files[-1].name if mem_files else '无'}")
    if age > args.max_age:
        print(f"⚠ 已超过 {args.max_age} 天未导出：Settings → Privacy → Export data，收到邮件后 24 小时内下载。")
        return 1
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    root: Path = args.root
    text = Path(args.from_file).read_text(encoding="utf-8") if args.from_file else sys.stdin.read()
    if not text.strip():
        print("没有读到内容。用法: pbpaste | python3 claude_backup.py memory")
        return 1
    mem_dir = root / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    previous = sorted(mem_dir.glob("*.md"))
    target = unique_path(mem_dir, _dt.date.today().isoformat(), ".md")
    target.write_text(text.rstrip() + "\n", encoding="utf-8")
    print(f"已保存 → {target}")
    if previous:
        old = previous[-1].read_text(encoding="utf-8").splitlines()
        delta = list(difflib.unified_diff(old, text.rstrip().splitlines(), previous[-1].name, target.name, lineterm=""))
        print("\n".join(delta) if delta else f"与 {previous[-1].name} 相比无变化。")
    return 0


# ---------------------------------------------------------------- publish

GITHUB_WARN_BYTES = 50 * 1024 * 1024  # GitHub 超过 50MB 警告，100MB 拒收


def _write_archive_index(archive: Path, catalog: Dict[str, Dict[str, Any]]) -> None:
    rows = []
    for uid, info in sorted(catalog.items(), key=lambda kv: kv[1].get("updated_at", ""), reverse=True):
        mark = " ⚠ 账号中已删除" if info.get("deleted_in_account") else ""
        title = (info.get("name") or "（无标题）").replace("|", "/")
        rows.append(f"| {pretty_time(info.get('updated_at'))} | [{title}]({info['file']}){mark} | "
                    f"{info.get('messages', '')} |")
    (archive / "index.md").write_text(
        "# 对话索引（自动生成，勿手改）\n\n按最近更新排序。标记“账号中已删除”的对话仅存在于本备份。\n\n"
        "| 更新时间 | 标题 | 消息数 |\n|---|---|---|\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def cmd_publish(args: argparse.Namespace) -> int:
    """把最近一次导入的快照合并进备份仓库的 archive/，以及把记忆存档复制到 memory/。

    对话按 UUID 累积：同一对话更新则覆盖（历史版本由 git 保留），账号里删掉的对话保留并标记。
    不提交 git，提交由 sync.sh 完成。
    """
    root: Path = args.root
    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").exists():
        print(f"{repo} 不是 git 仓库。")
        return 1
    snaps = sorted((root / "exports").glob("*/manifest.json"))
    if not snaps:
        print(f"{root} 下没有导入过的快照，先运行 import。")
        return 1
    snap_dir = snaps[-1].parent
    manifest = json.loads(snaps[-1].read_text(encoding="utf-8"))

    archive = repo / "archive"
    conv_dir = archive / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = archive / "catalog.json"
    catalog: Dict[str, Dict[str, Any]] = (
        json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {}
    )
    history_path = archive / "snapshots.jsonl"
    published = set()
    if history_path.exists():
        published = {json.loads(l)["snapshot"] for l in history_path.read_text(encoding="utf-8").splitlines() if l}

    added = updated = 0
    current = manifest["conversations"]
    for uid, info in current.items():
        src = snap_dir / info["file"]
        dest_rel = f"conversations/{Path(info['file']).name}"
        old = catalog.get(uid)
        if old and old.get("file") != dest_rel:
            (archive / old["file"]).unlink(missing_ok=True)  # 对话改名：删旧文件名
        if not old:
            added += 1
        elif old.get("updated_at") != info["updated_at"] or old.get("file") != dest_rel:
            updated += 1
        shutil.copy2(src, archive / dest_rel)
        catalog[uid] = {
            "name": info["name"], "updated_at": info["updated_at"], "messages": info["messages"],
            "file": dest_rel, "first_seen": (old or {}).get("first_seen", manifest["snapshot"]),
            "last_seen": manifest["snapshot"], "deleted_in_account": False,
        }
    newly_deleted = 0
    for uid, info in catalog.items():
        if uid not in current and not info.get("deleted_in_account"):
            info["deleted_in_account"] = True
            newly_deleted += 1

    # 导出中的项目与记忆：镜像最新一份，供对照（源头版本在 projects/ 和 memory/）
    if (snap_dir / "projects").exists():
        shutil.rmtree(archive / "projects", ignore_errors=True)
        shutil.copytree(snap_dir / "projects", archive / "projects")
    if (snap_dir / "memory" / "memory.md").exists():
        shutil.copy2(snap_dir / "memory" / "memory.md", archive / "export-memory.md")

    copied_mem = 0
    for mem in sorted((root / "memory").glob("*.md")):
        target = repo / "memory" / mem.name
        if not target.exists() or target.read_bytes() != mem.read_bytes():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(mem, target)
            copied_mem += 1

    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_archive_index(archive, catalog)
    if manifest["snapshot"] not in published:
        with history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"snapshot": manifest["snapshot"], "imported_at": manifest["imported_at"],
                                 "raw_sha256": (manifest.get("raw") or {}).get("sha256"),
                                 "counts": manifest["counts"]}, ensure_ascii=False) + "\n")

    big = [p for p in repo.rglob("*") if ".git" not in p.parts and p.is_file()
           and p.stat().st_size > GITHUB_WARN_BYTES]
    print(f"已合并快照 {manifest['snapshot']} → {repo}")
    print(f"  对话: 新增 {added}，更新 {updated}，新标记为账号中已删除 {newly_deleted}，累计 {len(catalog)}")
    print(f"  记忆存档复制 {copied_mem} 份")
    if big:
        print("  ⚠ 以下文件超过 50MB，GitHub 推送可能被拒（上限 100MB）：")
        for p in big:
            print(f"    - {p.relative_to(repo)} ({p.stat().st_size // (1024 * 1024)}MB)")
        return 1
    return 0


# ---------------------------------------------------------------- cli

def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=lambda p: Path(p).expanduser(), default=DEFAULT_ROOT,
                        help=f"备份数据目录（默认 {DEFAULT_ROOT}）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_import = sub.add_parser("import", help="导入官方导出 zip 或目录")
    p_import.add_argument("source")
    p_import.set_defaults(func=cmd_import)

    p_status = sub.add_parser("status", help="查看最近备份，过期则返回 1")
    p_status.add_argument("--max-age", type=int, default=7)
    p_status.set_defaults(func=cmd_status)

    p_mem = sub.add_parser("memory", help="保存手动复制的记忆文本")
    p_mem.add_argument("--from", dest="from_file")
    p_mem.set_defaults(func=cmd_memory)

    p_pub = sub.add_parser("publish", help="把最近快照合并进备份仓库（如 Claudebak）")
    p_pub.add_argument("repo", nargs="?", default=os.environ.get("CLAUDE_BACKUP_REPO", "~/claudebak"))
    p_pub.set_defaults(func=cmd_publish)

    args = parser.parse_args(list(argv) if argv is not None else None)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
