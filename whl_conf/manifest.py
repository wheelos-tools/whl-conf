import yaml
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Iterator, List
import logging


class ManifestError(Exception):
    """Base class for Manifest-related errors."""
    pass


class ManifestFileNotFoundError(ManifestError):
    """Error raised when manifest.yaml is not found."""
    pass


class ManifestFormatError(ManifestError):
    """Error raised for formatting issues within manifest.yaml."""
    pass


class FileMapping:
    """Represents a single source-to-destination file mapping."""

    def __init__(self, src: str, dest: str, description: str = ""):
        if not src or not dest:
            raise ValueError("Source and destination paths cannot be empty.")
        self.src = src
        self.dest = dest
        self.description = description

    def to_dict(self) -> Dict[str, str]:
        data = {'src': self.src, 'dest': self.dest}
        if self.description:
            data['description'] = self.description
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FileMapping':
        if 'src' not in data or 'dest' not in data:
            raise ManifestFormatError(
                f"Mapping is missing 'src' or 'dest': {data}")
        return cls(src=data['src'], dest=data['dest'], description=data.get('description', ''))

    def __repr__(self) -> str:
        return f"FileMapping(src='{self.src}', dest='{self.dest}')"


class ManifestManager:
    """
    Manages the manifest.yaml file for a configuration.

    This class enforces uniqueness for source paths and provides a robust,
    dictionary-like interface for managing file mappings.
    """
    MANIFEST_FILENAME = "manifest.yaml"

    def __init__(self, config_path: Path):
        """
        Initializes the ManifestManager.

        It attempts to load the manifest upon instantiation if it exists.
        If not, it initializes as an empty manifest.

        Args:
            config_path: The absolute path to the configuration directory.
        """
        self.config_path = config_path
        self.manifest_path = self.config_path / self.MANIFEST_FILENAME
        # Use a dictionary for O(1) lookups and inherent uniqueness of src
        self._mappings: Dict[str, FileMapping] = {}

        if self.exists():
            try:
                self.load()
            except ManifestError as e:
                logging.error(
                    f"Failed to auto-load manifest at '{self.manifest_path}': {e}")
                # Initialize empty on load failure to ensure a valid state
                self._mappings = {}

    def exists(self) -> bool:
        """Checks if the meta.yaml file exists on disk."""
        return self.manifest_path.is_file()

    def load(self):
        """
        Loads and parses the manifest.yaml file into memory.
        Raises:
            ManifestFileNotFoundError: If the manifest file does not exist.
            ManifestFormatError: On parsing or structure validation errors.
        """
        if not self.exists():
            raise ManifestFileNotFoundError(
                f"Manifest file not found: {self.manifest_path}")

        try:
            with self.manifest_path.open('r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ManifestFormatError(
                f"YAML syntax error in '{self.manifest_path}': {e}") from e

        if not data or 'files' not in data:
            self._mappings = {}  # Treat empty or invalid file as an empty manifest
            return

        if not isinstance(data['files'], list):
            raise ManifestFormatError(
                f"'files' key must be a list, but got {type(data['files'])}.")

        # Atomically update mappings
        new_mappings = {}
        for item in data['files']:
            try:
                mapping = FileMapping.from_dict(item)
                if mapping.src in new_mappings:
                    logging.warning(
                        f"Duplicate source '{mapping.src}' found in manifest. Using last entry.")
                new_mappings[mapping.src] = mapping
            except (ManifestFormatError, ValueError) as e:
                raise ManifestFormatError(
                    f"Invalid file mapping entry: {item}. Reason: {e}") from e

        self._mappings = new_mappings
        logging.debug(
            f"Manifest loaded with {len(self._mappings)} unique file mappings.")

    def save(self):
        """Saves the current file mappings to manifest.yaml."""
        self.config_path.mkdir(parents=True, exist_ok=True)

        # Sort by src path for consistent, human-readable output
        sorted_mappings = sorted(self._mappings.values(), key=lambda m: m.src)
        data_to_dump = {'files': [m.to_dict() for m in sorted_mappings]}

        try:
            with self.manifest_path.open('w', encoding='utf-8') as f:
                yaml.safe_dump(data_to_dump, f, allow_unicode=True,
                               default_flow_style=False, indent=2)
        except IOError as e:
            raise ManifestError(
                f"Failed to write to manifest file '{self.manifest_path}': {e}") from e

    @property
    def file_mappings(self) -> Tuple[FileMapping, ...]:
        """Returns an immutable tuple of all file mappings."""
        return tuple(self._mappings.values())

    def set_mapping(self, mapping: FileMapping):
        """Adds a new file mapping or updates an existing one based on `src`."""
        self._mappings[mapping.src] = mapping

    def remove_mapping(self, src: str) -> bool:
        """Removes a file mapping by its source path. Returns True if removed."""
        if src in self._mappings:
            del self._mappings[src]
            return True
        return False

    def get_mapping(self, src: str) -> Optional[FileMapping]:
        """Retrieves a file mapping by its source path."""
        return self._mappings.get(src)

    def validate_source_files_exist(self) -> List[str]:
        """Checks if all source files listed in the manifest exist on disk."""
        return [src for src in self._mappings if not (self.config_path / src).is_file()]

    def compare_with(self, other: 'ManifestManager') -> Dict[str, Any]:
        """Compares this manifest with another, returning a structured difference."""
        self_keys = set(self._mappings.keys())
        other_keys = set(other.get_all_source_paths())

        return {
            'only_in_self': list(self_keys - other_keys),
            'only_in_other': list(other_keys - self_keys),
            'common_files': list(self_keys & other_keys),
            'different_destinations': [
                src for src in (self_keys & other_keys)
                if self.get_mapping(src).dest != other.get_mapping(src).dest
            ]
        }

    def summary_info(self) -> Dict[str, Any]:
        """
        Returns a summary of the manifest, including the number of mappings
        and a list of all source paths.
        """
        return {
            'filesCount': len(self._mappings)
        }

    def get_all_source_paths(self) -> Tuple[str, ...]:
        """Returns a tuple of all source file paths."""
        return tuple(self._mappings.keys())

    def __len__(self) -> int:
        """Returns the number of unique file mappings."""
        return len(self._mappings)

    def __contains__(self, src: str) -> bool:
        """Allows `if 'path/to/file.txt' in manager:` syntax."""
        return src in self._mappings

    def __getitem__(self, src: str) -> FileMapping:
        """Allows `manager['path/to/file.txt']` access."""
        return self._mappings[src]

    def __iter__(self) -> Iterator[FileMapping]:
        """Allows iteration over the FileMapping objects."""
        return iter(self.file_mappings)
