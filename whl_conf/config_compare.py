import hashlib
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, Set, List


def _calculate_sha256(file_path: Path, block_size=65536) -> str:
    """Calculates the SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, 'rb') as f:
            for block in iter(lambda: f.read(block_size), b''):
                sha256.update(block)
        return sha256.hexdigest()
    except (IOError, OSError):
        return ""  # Return empty string if file is unreadable


@dataclass(frozen=True)
class FileMetadata:
    """A read-only dataclass to hold file metadata for comparison."""
    relative_path: Path
    size: int
    mtime: float
    sha256: str = ""  # Lazily computed if needed

    def __hash__(self):
        return hash(self.relative_path)

    def __eq__(self, other):
        if not isinstance(other, FileMetadata):
            return NotImplemented
        return self.relative_path == other.relative_path


class Colors:
    """A simple helper class for adding color to terminal output."""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    ENDC = '\033[0m'

    # Helper to disable colors if the terminal doesn't support them
    @staticmethod
    def disable():
        for attr in dir(Colors):
            if attr.isupper():
                setattr(Colors, attr, "")


class ConfigComparator:
    """
    A dedicated class for performing a neutral, side-by-side comparison
    of two configuration directories. It can return structured data
    or a formatted, human-readable report.
    """

    def __init__(self, path1: Path, path2: Path):
        self.path1 = path1
        self.path2 = path2
        self.name1 = path1.name
        self.name2 = path2.name

    def _scan_directory(self, root_path: Path) -> Dict[Path, FileMetadata]:
        """Scans a directory recursively and gathers metadata for each file."""
        files_metadata = {}
        for item in root_path.rglob('*'):
            if item.is_file() and item.name != "meta.yaml":
                relative_path = item.relative_to(root_path)
                stat = item.stat()
                files_metadata[relative_path] = FileMetadata(
                    relative_path=relative_path,
                    size=stat.st_size,
                    mtime=stat.st_mtime
                )
        return files_metadata

    def compare(self) -> Dict[str, Any]:
        """
        Performs a neutral comparison and returns a structured dictionary of differences.
        """
        inventory1 = self._scan_directory(self.path1)
        inventory2 = self._scan_directory(self.path2)

        files1_paths: Set[Path] = set(inventory1.keys())
        files2_paths: Set[Path] = set(inventory2.keys())

        common_paths: Set[Path] = files1_paths.intersection(files2_paths)
        only_in_1_paths: Set[Path] = files1_paths - files2_paths
        only_in_2_paths: Set[Path] = files2_paths - files1_paths

        content_differs = []
        identical = []

        for rel_path in common_paths:
            meta1 = inventory1[rel_path]
            meta2 = inventory2[rel_path]

            if meta1.size == meta2.size and meta1.mtime == meta2.mtime:
                identical.append(str(rel_path))
                continue

            hash1 = _calculate_sha256(self.path1 / rel_path)
            hash2 = _calculate_sha256(self.path2 / rel_path)

            if hash1 and hash1 == hash2:
                identical.append(str(rel_path))
            else:
                content_differs.append({
                    "path": str(rel_path),
                    self.name1: {"size": meta1.size, "mtime": meta1.mtime, "sha256": hash1},
                    self.name2: {"size": meta2.size, "mtime": meta2.mtime, "sha256": hash2},
                })

        return {
            "only_in_config1": sorted([str(p) for p in only_in_1_paths]),
            "only_in_config2": sorted([str(p) for p in only_in_2_paths]),
            "common_files": {
                "identical": sorted(identical),
                "different": content_differs,
            }
        }

    def format_report(self) -> str:
        """
        Generates a human-readable, colored string report from the comparison data.

        Returns:
            A formatted string ready to be printed to the console.
        """
        report_data = self.compare()
        report_lines: List[str] = []

        # Helper function for appending lines
        def add(line):
            report_lines.append(line)

        # --- Header ---
        add(f"{Colors.BOLD}{Colors.BLUE}Comparing Configurations:{Colors.ENDC}")
        add(f"  (a) {self.name1}")
        add(f"  (b) {self.name2}")

        # --- Summary ---
        add(f"\n{Colors.BOLD}[ SUMMARY ]{Colors.ENDC}")
        only_in_1 = report_data["only_in_config1"]
        only_in_2 = report_data["only_in_config2"]
        common = report_data["common_files"]
        identical_count = len(common['identical'])
        different_count = len(common['different'])

        add(f"  • Files only in (a): {len(only_in_1)}")
        add(f"  • Files only in (b): {len(only_in_2)}")
        add(f"  • Common Files: {identical_count + different_count} ({identical_count} identical, {different_count} different)")

        # --- Details ---
        add(f"\n{Colors.BOLD}[ DETAILS ]{Colors.ENDC}")

        if only_in_1:
            add(f"\n  ── Files only in '{self.name1}' ──")
            for path in only_in_1:
                add(f"    {Colors.RED}-  {path}{Colors.ENDC}")

        if only_in_2:
            add(f"\n  ── Files only in '{self.name2}' ──")
            for path in only_in_2:
                add(f"    {Colors.GREEN}+  {path}{Colors.ENDC}")

        if common['different']:
            add(f"\n  ── Common Files with Different Content ──")
            for diff_item in common['different']:
                add(f"    {Colors.YELLOW}~  {diff_item['path']}{Colors.ENDC}")
                info1 = diff_item[self.name1]
                info2 = diff_item[self.name2]
                add(
                    f"        (a): {info1['size']/1024:.2f} KB | sha256: {info1['sha256'][:10]}...")
                add(
                    f"        (b): {info2['size']/1024:.2f} KB | sha256: {info2['sha256'][:10]}...")

        if common['identical']:
            add(f"\n  ── {len(common['identical'])} identical file(s) not shown ──")

        return "\n".join(report_lines)
