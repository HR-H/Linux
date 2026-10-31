import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_backup as cb  # noqa: E402


def conv(uid, name, updated, messages):
    return {"uuid": uid, "name": name, "created_at": "2026-10-01T08:00:00Z",
            "updated_at": updated, "chat_messages": messages}


MSGS = [
    {"sender": "human", "created_at": "2026-10-01T08:00:00Z", "text": "写个分镜",
     "content": [{"type": "text", "text": "写个分镜"}],
     "attachments": [{"file_name": "剧本.txt", "extracted_content": "第一场 ```代码```"}], "files": []},
    {"sender": "assistant", "created_at": "2026-10-01T08:00:05Z", "text": "",
     "content": [{"type": "thinking", "thinking": "先想想"},
                 {"type": "text", "text": "镜头1：远景"},
                 {"type": "tool_use", "name": "search", "input": {"q": "x"}},
                 {"type": "weird_new_block", "foo": 1}]},
]


def make_zip(path, conversations, with_extras=True):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("data/conversations.json", json.dumps(conversations, ensure_ascii=False))
        if with_extras:
            zf.writestr("data/projects.json", json.dumps([{
                "uuid": "p-1234567890", "name": "短剧/项目", "description": "描述",
                "prompt_template": "你是分镜师", "docs": [{"filename": "规则.md", "content": "规则正文"}],
            }], ensure_ascii=False))
            zf.writestr("data/users.json", json.dumps([{"uuid": "u1"}]))
            zf.writestr("data/memories.json", json.dumps([{
                "conversations_memory": "用户在做短剧", "project_memories": {"p-1234567890": "项目记忆"}}],
                ensure_ascii=False))
            zf.writestr("../evil.json", "{}")  # 不应被解压到外部


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.root = self.dir / "backup"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cb.main(["--root", str(self.root), *args])
        return code, buf.getvalue()

    def test_full_import_and_diff(self):
        z1 = self.dir / "export1.zip"
        make_zip(z1, [conv("aaaaaaaa-1", "分镜: 第一集?", "2026-10-01T09:00:00Z", MSGS),
                      conv("bbbbbbbb-2", "将被删除", "2026-10-02T09:00:00Z", MSGS[:1])])
        code, out = self.run_cli("import", str(z1))
        self.assertEqual(code, 0, out)

        snap = next(p for p in (self.root / "exports").iterdir() if p.name != "raw")
        md_files = list((snap / "conversations").glob("*.md"))
        self.assertEqual(len(md_files), 2)
        self.assertTrue(all("/" not in p.name and ":" not in p.name and "?" not in p.name for p in md_files))
        text = next(p for p in md_files if "aaaaaaaa" in p.name).read_text(encoding="utf-8")
        for expected in ("镜头1：远景", "思考过程", "工具调用：search", "weird_new_block", "附件：剧本.txt", "````"):
            self.assertIn(expected, text)

        proj = next((snap / "projects").iterdir())
        self.assertIn("你是分镜师", (proj / "instructions.md").read_text(encoding="utf-8"))
        self.assertEqual((proj / "docs" / "规则.md").read_text(encoding="utf-8"), "规则正文")
        self.assertIn("项目记忆", (snap / "memory" / "memory.md").read_text(encoding="utf-8"))
        self.assertTrue((snap / "index.md").exists())
        self.assertFalse((self.dir / "evil.json").exists())

        manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["counts"]["conversations"], 2)
        self.assertEqual(len(manifest["raw"]["sha256"]), 64)
        self.assertIn("../evil.json", manifest["unrecognized_files"])

        # 第二次导出：一个更新、一个消失、一个新增
        z2 = self.dir / "export2.zip"
        make_zip(z2, [conv("aaaaaaaa-1", "分镜: 第一集?", "2026-10-05T09:00:00Z", MSGS),
                      conv("cccccccc-3", "新对话", "2026-10-05T10:00:00Z", MSGS)], with_extras=False)
        code, out = self.run_cli("import", str(z2))
        self.assertEqual(code, 0, out)
        self.assertIn("新增 1，更新 1，消失 1", out)
        self.assertIn("将被删除", out)
        self.assertEqual(len([p for p in (self.root / "exports").iterdir() if p.name != "raw"]), 2)

    def test_directory_source_and_missing_conversations(self):
        d = self.dir / "unzipped"
        d.mkdir()
        (d / "conversations.json").write_text(json.dumps([conv("x", None, None, [])]), encoding="utf-8")
        code, _ = self.run_cli("import", str(d))
        self.assertEqual(code, 0)
        (self.dir / "empty").mkdir()
        with self.assertRaises(SystemExit):
            self.run_cli("import", str(self.dir / "empty"))

    def test_status_and_memory(self):
        code, out = self.run_cli("status")
        self.assertEqual(code, 1)
        z = self.dir / "e.zip"
        make_zip(z, [conv("a", "t", "2026-10-01T00:00:00Z", [])], with_extras=False)
        self.run_cli("import", str(z))
        code, out = self.run_cli("status")
        self.assertEqual(code, 0, out)

        m1 = self.dir / "m1.txt"
        m1.write_text("偏好A\n偏好B\n", encoding="utf-8")
        self.run_cli("memory", "--from", str(m1))
        m1.write_text("偏好A\n偏好C\n", encoding="utf-8")
        code, out = self.run_cli("memory", "--from", str(m1))
        self.assertEqual(code, 0)
        self.assertIn("-偏好B", out)
        self.assertIn("+偏好C", out)


if __name__ == "__main__":
    unittest.main()
