#!/usr/bin/env python

# Copyright 2025 The WheelOS Team. All Rights Reserved.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import subprocess
import tempfile
import unittest
from pathlib import Path

from whl_conf.config import ConfigManager


def run_git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository)] + list(arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        universal_newlines=True,
    )
    return result.stdout


def commit_files(repository: Path, base_dir: Path, files):
    for relative_path, content in files.items():
        path = base_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    run_git(repository, "add", "--", "apollo")
    run_git(repository, "commit", "--quiet", "-m", "Add tracked config files")


def create_config(base_dir: Path, name: str, files):
    config_dir = base_dir / "data" / "confs" / name
    config_dir.mkdir(parents=True, exist_ok=True)
    for relative_path, content in files.items():
        path = config_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def index_flag(repository: Path, relative_path: str) -> str:
    output = run_git(repository, "ls-files", "-v", "--", relative_path)
    return output[:1]


def status_paths(repository: Path) -> str:
    return run_git(repository, "status", "--porcelain")


def git_path(repository: Path, relative_git_path: str) -> Path:
    path = Path(
        run_git(repository, "rev-parse", "--git-path", relative_git_path).strip()
    )
    if not path.is_absolute():
        path = repository / path
    return path


class GitIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.temp_path = Path(self.temporary_directory.name)

        # The repository root intentionally differs from
        # ConfigManager.base_dir.
        self.repository = self.temp_path / "repository"
        self.base_dir = self.repository / "apollo"
        self.base_dir.mkdir(parents=True)

        run_git(self.repository, "init", "--quiet")
        run_git(self.repository, "config", "user.name", "whl-conf tests")
        run_git(
            self.repository,
            "config",
            "user.email",
            "whl-conf@example.com",
        )

        gitignore = self.repository / ".gitignore"
        gitignore.write_text("apollo/data/confs/\n", encoding="utf-8")
        run_git(self.repository, "add", ".gitignore")
        run_git(
            self.repository,
            "commit",
            "--quiet",
            "-m",
            "Initialize test repository",
        )

        self.exclude_path = git_path(self.repository, "info/exclude")
        with self.exclude_path.open("a", encoding="utf-8") as exclude_file:
            exclude_file.write("/user-owned-path\n")

    def test_activate_tracked_file_sets_skip_worktree(self):
        commit_files(
            self.repository,
            self.base_dir,
            {"foo.conf": "repository version\n"},
        )
        create_config(
            self.base_dir, "vehicle_a", {"foo.conf": "vehicle A\n"}
        )

        manager = ConfigManager(str(self.base_dir))
        manager.activate_config("vehicle_a")

        self.assertTrue((self.base_dir / "foo.conf").is_symlink())
        self.assertEqual(
            (self.base_dir / "foo.conf").read_text(encoding="utf-8"),
            "vehicle A\n",
        )
        self.assertEqual(index_flag(self.repository, "apollo/foo.conf"), "S")
        self.assertNotIn("foo.conf", status_paths(self.repository))

        # Re-activating an already active configuration remains idempotent.
        manager.activate_config("vehicle_a")
        self.assertTrue((self.base_dir / "foo.conf").is_symlink())
        self.assertEqual(index_flag(self.repository, "apollo/foo.conf"), "S")
        self.assertNotIn("foo.conf", status_paths(self.repository))

    def test_remove_tracked_file_restores_index_version(self):
        commit_files(
            self.repository,
            self.base_dir,
            {"foo.conf": "repository version\n"},
        )
        create_config(
            self.base_dir, "vehicle_a", {"foo.conf": "vehicle A\n"}
        )

        manager = ConfigManager(str(self.base_dir))
        manager.activate_config("vehicle_a")
        # Passing the managed symlink itself exercises the non-dereferencing
        # path normalization used by remove_active_config.
        manager.remove_active_config([str(self.base_dir / "foo.conf")])

        restored_path = self.base_dir / "foo.conf"
        self.assertFalse(restored_path.is_symlink())
        self.assertEqual(
            restored_path.read_text(encoding="utf-8"), "repository version\n"
        )
        self.assertNotEqual(index_flag(self.repository, "apollo/foo.conf"), "S")
        self.assertEqual(status_paths(self.repository), "")

    def test_switch_config_restores_old_only_paths(self):
        commit_files(
            self.repository,
            self.base_dir,
            {"foo.conf": "repository foo\n", "bar.conf": "repository bar\n"},
        )
        create_config(
            self.base_dir,
            "vehicle_a",
            {"foo.conf": "vehicle A foo\n", "bar.conf": "vehicle A bar\n"},
        )
        create_config(
            self.base_dir, "vehicle_b", {"foo.conf": "vehicle B foo\n"}
        )

        manager = ConfigManager(str(self.base_dir))
        manager.activate_config("vehicle_a")
        manager.activate_config("vehicle_b")

        foo_path = self.base_dir / "foo.conf"
        bar_path = self.base_dir / "bar.conf"
        self.assertTrue(foo_path.is_symlink())
        self.assertEqual(
            foo_path.read_text(encoding="utf-8"), "vehicle B foo\n"
        )
        self.assertEqual(index_flag(self.repository, "apollo/foo.conf"), "S")
        self.assertFalse(bar_path.is_symlink())
        self.assertEqual(
            bar_path.read_text(encoding="utf-8"), "repository bar\n"
        )
        self.assertNotEqual(index_flag(self.repository, "apollo/bar.conf"), "S")
        self.assertEqual(status_paths(self.repository), "")

    def test_untracked_files_are_hidden_by_managed_exclude_block(self):
        special_relative_path = "folder/local [1].conf"
        create_config(
            self.base_dir,
            "vehicle_a",
            {
                "local.conf": "local config\n",
                special_relative_path: "special config\n",
            },
        )

        manager = ConfigManager(str(self.base_dir))
        manager.activate_config("vehicle_a")

        managed_path = self.base_dir / "local.conf"
        special_managed_path = self.base_dir / special_relative_path
        self.assertTrue(managed_path.is_symlink())
        self.assertTrue(special_managed_path.is_symlink())
        self.assertEqual(
            run_git(
                self.repository,
                "ls-files",
                "--",
                "apollo/local.conf",
            ),
            "",
        )
        self.assertEqual(status_paths(self.repository), "")

        exclude_content = self.exclude_path.read_text(encoding="utf-8")
        self.assertIn("/user-owned-path\n", exclude_content)
        self.assertIn("# BEGIN whl-conf managed paths\n", exclude_content)
        self.assertIn("/apollo/local.conf\n", exclude_content)
        self.assertIn(
            "/apollo/folder/local\\ \\[1\\].conf\n", exclude_content
        )

        manager.remove_active_config([str(managed_path)])
        self.assertFalse(managed_path.exists())
        self.assertFalse(managed_path.is_symlink())
        self.assertTrue(special_managed_path.is_symlink())
        self.assertEqual(status_paths(self.repository), "")

        exclude_content = self.exclude_path.read_text(encoding="utf-8")
        self.assertNotIn("/apollo/local.conf\n", exclude_content)
        self.assertIn(
            "/apollo/folder/local\\ \\[1\\].conf\n", exclude_content
        )

        manager.remove_active_config([str(special_managed_path)])
        self.assertFalse(special_managed_path.exists())
        self.assertEqual(status_paths(self.repository), "")
        exclude_content = self.exclude_path.read_text(encoding="utf-8")
        self.assertIn("/user-owned-path\n", exclude_content)
        self.assertNotIn("# BEGIN whl-conf managed paths", exclude_content)

    def test_switch_replaces_untracked_exclude_paths(self):
        create_config(
            self.base_dir,
            "vehicle_a",
            {"shared.conf": "vehicle A\n", "old.conf": "old\n"},
        )
        create_config(
            self.base_dir,
            "vehicle_b",
            {"shared.conf": "vehicle B\n", "new.conf": "new\n"},
        )

        manager = ConfigManager(str(self.base_dir))
        manager.activate_config("vehicle_a")
        manager.activate_config("vehicle_b")

        self.assertTrue((self.base_dir / "shared.conf").is_symlink())
        self.assertTrue((self.base_dir / "new.conf").is_symlink())
        self.assertFalse((self.base_dir / "old.conf").exists())
        self.assertEqual(status_paths(self.repository), "")

        exclude_content = self.exclude_path.read_text(encoding="utf-8")
        self.assertIn("/apollo/shared.conf\n", exclude_content)
        self.assertIn("/apollo/new.conf\n", exclude_content)
        self.assertNotIn("/apollo/old.conf\n", exclude_content)
        self.assertIn("/user-owned-path\n", exclude_content)
        self.assertEqual(
            exclude_content.count("# BEGIN whl-conf managed paths"), 1
        )

    def test_add_and_remove_untracked_path_updates_exclude_block(self):
        create_config(self.base_dir, "vehicle_a", {})
        untracked_path = self.base_dir / "added-local.conf"
        untracked_path.write_text("local config\n", encoding="utf-8")

        manager = ConfigManager(str(self.base_dir))
        manager.activate_config("vehicle_a")
        self.assertNotEqual(status_paths(self.repository), "")

        manager.add_active_config([str(untracked_path)])
        self.assertTrue(untracked_path.is_symlink())
        self.assertEqual(status_paths(self.repository), "")
        self.assertIn(
            "/apollo/added-local.conf\n",
            self.exclude_path.read_text(encoding="utf-8"),
        )

        manager.remove_active_config([str(untracked_path)])
        self.assertFalse(untracked_path.exists())
        self.assertEqual(status_paths(self.repository), "")
        exclude_content = self.exclude_path.read_text(encoding="utf-8")
        self.assertIn("/user-owned-path\n", exclude_content)
        self.assertNotIn("# BEGIN whl-conf managed paths", exclude_content)

    def test_add_and_remove_tracked_path_with_special_characters(self):
        relative_path = "folder/special [1].conf"
        repository_path = "apollo/{}".format(relative_path)
        commit_files(
            self.repository,
            self.base_dir,
            {relative_path: "repository version\n"},
        )
        create_config(self.base_dir, "vehicle_a", {})

        manager = ConfigManager(str(self.base_dir))
        manager.activate_config("vehicle_a")
        managed_path = self.base_dir / relative_path
        manager.add_active_config([str(managed_path)])

        self.assertTrue(managed_path.is_symlink())
        self.assertEqual(index_flag(self.repository, repository_path), "S")
        self.assertNotIn(relative_path, status_paths(self.repository))

        manager.remove_active_config([str(managed_path)])
        self.assertFalse(managed_path.is_symlink())
        self.assertEqual(
            managed_path.read_text(encoding="utf-8"), "repository version\n"
        )
        self.assertNotEqual(index_flag(self.repository, repository_path), "S")
        self.assertEqual(status_paths(self.repository), "")

    def test_non_git_directory_keeps_existing_behavior(self):
        base_dir = self.temp_path / "not-a-repository"
        base_dir.mkdir()
        create_config(base_dir, "vehicle_a", {"local.conf": "local config\n"})

        manager = ConfigManager(str(base_dir))
        self.assertFalse(manager.git_worktree.is_git_worktree())

        manager.activate_config("vehicle_a")
        managed_path = base_dir / "local.conf"
        self.assertTrue(managed_path.is_symlink())

        manager.remove_active_config([str(managed_path)])
        self.assertFalse(managed_path.exists())
        self.assertFalse(managed_path.is_symlink())

    def test_remove_restores_staged_index_content_instead_of_head(self):
        commit_files(
            self.repository, self.base_dir, {"foo.conf": "version A\n"}
        )

        tracked_path = self.base_dir / "foo.conf"
        tracked_path.write_text("version B\n", encoding="utf-8")
        run_git(self.repository, "add", "--", "apollo/foo.conf")
        create_config(
            self.base_dir, "vehicle_a", {"foo.conf": "vehicle A\n"}
        )

        manager = ConfigManager(str(self.base_dir))
        manager.activate_config("vehicle_a")
        manager.remove_active_config([str(tracked_path)])

        self.assertFalse(tracked_path.is_symlink())
        self.assertEqual(
            tracked_path.read_text(encoding="utf-8"), "version B\n"
        )
        self.assertNotEqual(index_flag(self.repository, "apollo/foo.conf"), "S")
        # The staged index change is intentionally preserved.
        self.assertEqual(status_paths(self.repository), "M  apollo/foo.conf\n")


if __name__ == "__main__":
    unittest.main()
