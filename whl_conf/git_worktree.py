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

"""Git worktree integration for files managed by whl-conf."""

import os
import posixpath
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple


class GitWorktreeError(RuntimeError):
    """Raised when an operation against a detected Git worktree fails."""


class GitWorktreeManager:
    """Manages Git index state for paths below a whl-conf base directory."""

    EXCLUDE_BLOCK_BEGIN = "# BEGIN whl-conf managed paths"
    EXCLUDE_BLOCK_END = "# END whl-conf managed paths"

    def __init__(self, base_dir: Path):
        self.base_dir = self._absolute_without_dereference(base_dir)
        self.git_root: Optional[Path] = None
        self.exclude_path: Optional[Path] = None
        self._base_prefix = ""
        self._discover_worktree()

    @staticmethod
    def _absolute_without_dereference(path: Path) -> Path:
        """Return a normalized absolute path without following symlinks."""
        return Path(os.path.abspath(os.fspath(path)))

    def _discover_worktree(self) -> None:
        """Find the worktree containing ``base_dir``, if one exists."""
        try:
            root_result = subprocess.run(
                [
                    "git",
                    "-C",
                    os.fspath(self.base_dir),
                    "rev-parse",
                    "--show-toplevel",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if root_result.returncode != 0:
                return

            prefix_result = subprocess.run(
                [
                    "git",
                    "-C",
                    os.fspath(self.base_dir),
                    "rev-parse",
                    "--show-prefix",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if prefix_result.returncode != 0:
                return
        except OSError:
            # Git is an optional enhancement. A missing executable is equivalent
            # to running outside a Git worktree.
            return

        root = os.fsdecode(root_result.stdout).rstrip("\r\n")
        if not root:
            return

        self.git_root = self._absolute_without_dereference(Path(root))
        self._base_prefix = os.fsdecode(prefix_result.stdout).rstrip("\r\n/")

        exclude_result = self._run(["rev-parse", "--git-path", "info/exclude"])
        if exclude_result is None:
            return
        exclude_path = Path(os.fsdecode(exclude_result.stdout).rstrip("\r\n"))
        if not exclude_path.is_absolute():
            exclude_path = self.git_root / exclude_path
        self.exclude_path = self._absolute_without_dereference(exclude_path)

    def is_git_worktree(self) -> bool:
        """Return whether ``base_dir`` is contained in a Git worktree."""
        return self.git_root is not None

    def _repository_paths(self, paths: Iterable[Path]) -> List[Tuple[Path, str]]:
        """Map paths below ``base_dir`` to normalized repository-relative paths."""
        if not self.is_git_worktree():
            return []

        mapped_paths: List[Tuple[Path, str]] = []
        seen = set()
        for path in paths:
            absolute_path = self._absolute_without_dereference(path)
            try:
                relative_to_base = absolute_path.relative_to(self.base_dir)
            except ValueError as e:
                raise GitWorktreeError(
                    "Cannot manage Git path '{}' because it is outside base directory "
                    "'{}'.".format(absolute_path, self.base_dir)
                ) from e

            relative_path = relative_to_base.as_posix()
            if self._base_prefix:
                relative_path = posixpath.join(self._base_prefix, relative_path)
            if relative_path in seen:
                continue
            seen.add(relative_path)
            mapped_paths.append((absolute_path, relative_path))
        return mapped_paths

    def _run(self, arguments: List[str], input_data: Optional[bytes] = None):
        """Run a Git command in the discovered worktree with useful errors."""
        if self.git_root is None:
            return None

        command = ["git", "-C", os.fspath(self.git_root)] + arguments
        try:
            result = subprocess.run(
                command,
                input=input_data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as e:
            raise GitWorktreeError(
                "Failed to execute Git command '{}': {}".format(
                    " ".join(command), e
                )
            ) from e

        if result.returncode != 0:
            stderr = os.fsdecode(result.stderr).strip()
            detail = stderr or "Git exited with status {}".format(result.returncode)
            raise GitWorktreeError(
                "Git command '{}' failed: {}".format(" ".join(command), detail)
            )
        return result

    def tracked_paths(self, paths: Iterable[Path]) -> List[Path]:
        """Return the supplied paths that already have Git index entries."""
        mapped_paths = self._repository_paths(paths)
        if not mapped_paths:
            return []

        # Read the index once and intersect in-process. This stays at one Git
        # subprocess even for a large manifest and avoids command-line length
        # limits. -z makes spaces, newlines, and other characters unambiguous.
        result = self._run(["ls-files", "--cached", "-z"])
        if result is None:
            return []

        tracked_repository_paths = {
            os.fsdecode(path) for path in result.stdout.split(b"\0") if path
        }
        return [
            absolute_path
            for absolute_path, repository_path in mapped_paths
            if repository_path in tracked_repository_paths
        ]

    def is_tracked(self, path: Path) -> bool:
        """Return whether a path already has a Git index entry."""
        return bool(self.tracked_paths([path]))

    def untracked_paths(self, paths: Iterable[Path]) -> List[Path]:
        """Return supplied paths that do not have Git index entries."""
        normalized_paths = [
            absolute_path
            for absolute_path, _ in self._repository_paths(paths)
        ]
        tracked_paths = set(self.tracked_paths(normalized_paths))
        return [path for path in normalized_paths if path not in tracked_paths]

    def _update_skip_worktree(self, paths: Iterable[Path], enabled: bool) -> None:
        mapped_paths = self._repository_paths(paths)
        if not mapped_paths:
            return

        option = "--skip-worktree" if enabled else "--no-skip-worktree"
        input_data = b"".join(
            os.fsencode(repository_path) + b"\0"
            for _, repository_path in mapped_paths
        )
        self._run(["update-index", option, "-z", "--stdin"], input_data)

    def set_skip_worktree(self, paths: Iterable[Path]) -> None:
        """Set skip-worktree for a batch of tracked paths."""
        self._update_skip_worktree(paths, enabled=True)

    def unset_skip_worktree(self, paths: Iterable[Path]) -> None:
        """Clear skip-worktree for a batch of tracked paths."""
        self._update_skip_worktree(paths, enabled=False)

    def restore_worktree(self, paths: Iterable[Path]) -> None:
        """Restore working-tree files from their current Git index entries."""
        mapped_paths = self._repository_paths(paths)
        if not mapped_paths:
            return

        pathspecs = [":(literal){}".format(path) for _, path in mapped_paths]
        self._run(["restore", "--worktree", "--"] + pathspecs)

    @staticmethod
    def _escape_exclude_path(repository_path: str) -> str:
        """Build an exact root-relative Git exclude pattern for one path."""
        if "\n" in repository_path or "\r" in repository_path:
            raise GitWorktreeError(
                "Cannot exclude Git path containing a newline: {!r}".format(
                    repository_path
                )
            )

        escaped_characters = set("\\*?[] ")
        escaped_path = "".join(
            "\\" + character
            if character in escaped_characters
            else character
            for character in repository_path
        )
        return "/" + escaped_path

    @classmethod
    def _remove_managed_exclude_blocks(cls, content: str) -> str:
        """Remove complete whl-conf blocks while preserving all user content."""
        output_lines: List[str] = []
        inside_managed_block = False
        for line in content.splitlines(keepends=True):
            marker = line.rstrip("\r\n")
            if marker == cls.EXCLUDE_BLOCK_BEGIN:
                if inside_managed_block:
                    raise GitWorktreeError(
                        "Malformed Git exclude file: nested whl-conf managed blocks."
                    )
                inside_managed_block = True
                continue
            if marker == cls.EXCLUDE_BLOCK_END:
                if not inside_managed_block:
                    raise GitWorktreeError(
                        "Malformed Git exclude file: whl-conf block end without start."
                    )
                inside_managed_block = False
                continue
            if not inside_managed_block:
                output_lines.append(line)

        if inside_managed_block:
            raise GitWorktreeError(
                "Malformed Git exclude file: unterminated whl-conf managed block."
            )
        return "".join(output_lines)

    def set_excluded_paths(self, paths: Iterable[Path]) -> None:
        """Replace whl-conf's ``info/exclude`` block with the supplied paths."""
        mapped_paths = self._repository_paths(paths)
        if not self.is_git_worktree():
            return
        if self.exclude_path is None:
            raise GitWorktreeError(
                "Git worktree was detected, but its info/exclude path was not found."
            )

        exclude_patterns: Set[str] = {
            self._escape_exclude_path(repository_path)
            for _, repository_path in mapped_paths
        }

        try:
            if self.exclude_path.exists():
                content = self.exclude_path.read_text(
                    encoding="utf-8", errors="surrogateescape"
                )
                existing_mode = self.exclude_path.stat().st_mode
            else:
                content = ""
                existing_mode = None

            updated_content = self._remove_managed_exclude_blocks(content)
            if exclude_patterns:
                if updated_content and not updated_content.endswith(("\n", "\r")):
                    updated_content += "\n"
                updated_content += self.EXCLUDE_BLOCK_BEGIN + "\n"
                updated_content += "\n".join(sorted(exclude_patterns)) + "\n"
                updated_content += self.EXCLUDE_BLOCK_END + "\n"

            if updated_content == content:
                return

            self.exclude_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.exclude_path.with_name(
                self.exclude_path.name + ".whl-conf-" + os.urandom(4).hex()
            )
            try:
                temporary_path.write_text(
                    updated_content,
                    encoding="utf-8",
                    errors="surrogateescape",
                )
                if existing_mode is not None:
                    os.chmod(os.fspath(temporary_path), existing_mode)
                os.replace(os.fspath(temporary_path), os.fspath(self.exclude_path))
            finally:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
        except GitWorktreeError:
            raise
        except OSError as e:
            raise GitWorktreeError(
                "Failed to update Git exclude file '{}': {}".format(
                    self.exclude_path, e
                )
            ) from e
