"""Tests verifying PROJECT_INDEX.md exists and matches scripts/generate_index.py."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_index  # noqa: E402


class TestProjectIndex(unittest.TestCase):
    """Verify PROJECT_INDEX.md presence, required sections, and drift check."""

    def test_project_index_exists_and_has_sections(self) -> None:
        index_path = REPO_ROOT / "PROJECT_INDEX.md"
        self.assertTrue(index_path.is_file(), "PROJECT_INDEX.md must exist at repo root")
        content = index_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        for heading in (
            "# quota-cockpit Project Index",
            "## 1. 按需求秒定位路由表",
            "## 2. HTTP API & 静态路由速查",
            "## 3. 持久化数据与外部只读数据源",
            "## 4. 分层模块目录总览",
            "## 5. 符号级精确行号索引",
        ):
            self.assertIn(heading, content)

    def test_generate_markdown_matches_disk(self) -> None:
        index_path = REPO_ROOT / "PROJECT_INDEX.md"
        actual = index_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        expected = generate_index.generate_markdown()
        self.assertEqual(actual, expected, "PROJECT_INDEX.md drifted from scripts/generate_index.py")

    def test_cli_check_exits_zero(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "generate_index.py"), "--check"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, f"--check failed:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("PROJECT_INDEX.md is up to date.", proc.stdout)


if __name__ == "__main__":
    unittest.main()
