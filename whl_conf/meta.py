import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
from datetime import datetime, timezone
import logging
import re
from dataclasses import dataclass, field, asdict

# --- Custom Exceptions (Unchanged, they are well-defined) ---


class MetaError(Exception):
    "Generic metadata error"


class MetaFileNotFoundError(MetaError):
    "Meta file not found"


class MetaFormatError(MetaError):
    "Meta file format error"


class MetaValidationError(MetaError):
    "Meta value validation error"


# --- Helper Functions (Improved for clarity and robustness) ---
def _to_iso(dt: datetime) -> str:
    """Converts a datetime object to a UTC ISO 8601 string with 'Z'."""
    if dt.tzinfo is None:
        # Assume naive datetimes are in the system's local timezone and convert to UTC
        dt = dt.astimezone(timezone.utc)
    else:
        # Convert timezone-aware datetimes to UTC
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _from_iso(s: str) -> Optional[datetime]:
    """Parses an ISO 8601 string (including 'Z' format) into a datetime object."""
    if not isinstance(s, str) or not s:
        return None
    try:
        # Handle 'Z' for UTC timezone directly, which is more robust
        if s.upper().endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        logging.warning(f"Could not parse timestamp '{s}'.")
        return None


def _is_semver_like(v: str) -> bool:
    """Validates if a string loosely follows semantic versioning patterns."""
    if not isinstance(v, str):
        return False
    semver_pattern = re.compile(
        r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$")
    return bool(semver_pattern.match(v))


# --- Data Models (Using dataclasses for clarity and automation) ---
@dataclass
class Maintainer:
    """A data model for a maintainer, with built-in validation."""
    name: str
    email: str = ""
    role: str = ""

    def __post_init__(self):
        """Perform validation and normalization after initialization."""
        if not self.name or not isinstance(self.name, str):
            raise MetaValidationError(
                "Maintainer 'name' must be a non-empty string.")
        self.name = self.name.strip()
        self.email = str(self.email).strip()
        self.role = str(self.role).strip()


@dataclass
class MetaInfo:
    """
    A self-validating data class for configuration metadata.
    It uses modern dataclasses to define its structure and handle serialization.
    """
    # --- Required & Core Fields ---
    version: str
    config_id: str
    created_at: datetime
    updated_at: datetime
    maintainers: List[Maintainer] = field(default_factory=list)

    # --- Optional Business/Context Fields ---
    vehicle_vin: Optional[str] = None
    hardware_hash: Optional[str] = None
    wheelos_hash: Optional[str] = None
    description: Optional[str] = None

    # --- Other Standard Optional Fields (can be removed if truly unused) ---
    notes: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    # --- Extra fields not part of the formal model ---
    extra_fields: Dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        """Post-initialization validation and normalization."""
        if not _is_semver_like(self.version):
            raise MetaValidationError(
                f"Version '{self.version}' is not a valid semantic version.")
        # Ensure tags are unique and sorted
        if self.tags:
            self.tags = sorted(list(set(str(t).strip()
                               for t in self.tags if str(t).strip())))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MetaInfo":
        """Factory method to create a MetaInfo instance from a dictionary."""
        if not isinstance(data, dict):
            raise MetaFormatError(
                f"Metadata must be a dictionary, got {type(data)}.")

        # Separate known fields from extra fields
        known_field_names = {f.name for f in cls.__dataclass_fields__.values()}
        init_data = {k: v for k, v in data.items() if k in known_field_names}
        extra_data = {k: v for k,
                      v in data.items() if k not in known_field_names}

        # --- Handle special field conversions ---
        # Maintainers: Convert list of dicts to list of Maintainer objects
        if 'maintainers' in init_data and isinstance(init_data['maintainers'], list):
            try:
                init_data['maintainers'] = [Maintainer(
                    **m) for m in init_data['maintainers']]
            except (TypeError, MetaValidationError) as e:
                raise MetaFormatError(
                    f"Invalid 'maintainers' structure: {e}") from e

        # Timestamps: Convert ISO strings to datetime objects
        now = datetime.now(timezone.utc)
        init_data['created_at'] = _from_iso(
            init_data.get('created_at', '')) or now
        # If updated_at is missing or invalid, it defaults to created_at
        init_data['updated_at'] = _from_iso(init_data.get(
            'updated_at', '')) or init_data['created_at']

        # Add extras
        init_data['extra_fields'] = extra_data

        try:
            return cls(**init_data)
        except (TypeError, MetaValidationError) as e:
            # Catches missing required fields like 'version' or 'config_id'
            raise MetaFormatError(
                f"Failed to create MetaInfo from dictionary: {e}") from e

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the object to a dictionary for YAML/JSON output."""
        # asdict correctly handles nested dataclasses
        data = asdict(self)

        # Pop the extra_fields container and merge its contents
        extras = data.pop('extra_fields', {})
        data.update(extras)

        # Convert datetimes to ISO strings
        data['created_at'] = _to_iso(self.created_at)
        data['updated_at'] = _to_iso(self.updated_at)

        # Filter out fields that are None or empty lists, except for required ones
        required_fields = {'version', 'config_id',
                           'created_at', 'updated_at', 'maintainers'}
        return {
            key: value for key, value in data.items()
            if value or key in required_fields
        }

    def pretty_print(self) -> None:
        """
        Prints the metadata in a clean, human-readable format to the console.
        This method fulfills the requirement of having a built-in pretty printer.
        """
        data = self.to_dict()

        # Define the desired order of keys for printing
        # This also filters which keys to show, matching the user request
        display_order = [
            'version', 'config_id', 'description', 'maintainers',
            'vehicle_vin', 'hardware_hash', 'wheelos_hash',
            'tags', 'notes', 'created_at', 'updated_at'
        ]

        # Calculate padding for clean alignment
        # We only consider the keys that are actually present in the data
        present_keys = [key for key in display_order if key in data]
        if not present_keys:
            print("  No metadata to display.")
            return

        max_key_length = max(len(key) for key in present_keys)

        print("  Metadata:")
        for key in display_order:
            if key in data:
                value = data[key]
                # Skip printing empty optional fields
                # Add other must-show fields if any
                if not value and key not in {'version', 'config_id'}:
                    continue

                # Format values for readability
                display_value = ""
                if isinstance(value, list):
                    if not value:
                        continue  # Don't print empty lists
                    if all(isinstance(v, dict) for v in value):
                        # Special, more detailed format for list of maintainers
                        display_value = "\n" + "\n".join(
                            f"{' ' * (max_key_length + 6)}- Name: {v.get('name', 'N/A')}, Email: {v.get('email', 'N/A')}, Role: {v.get('role', 'N/A')}"
                            for v in value
                        )
                    else:
                        display_value = ", ".join(map(str, value))
                else:
                    display_value = str(value)

                # Print the aligned key-value pair
                print(f"    {key:<{max_key_length}} : {display_value}")

# --- Manager Class (Largely unchanged, but now interacts with the new MetaInfo) ---


class MetaManager:
    """
    Manages the lifecycle (create, load, update, save) of a MetaInfo object
    associated with a configuration.
    """
    META_FILENAME = "meta.yaml"

    def __init__(self, config_path: Union[Path, str]):
        self.config_path = Path(config_path)
        self.meta_path = self.config_path / self.META_FILENAME
        self._meta_info: Optional[MetaInfo] = None
        # We remove the automatic load from __init__ to make the behavior
        # more predictable. Loading now happens explicitly or lazily.

    def exists(self) -> bool:
        """Checks if the meta.yaml file exists on disk."""
        return self.meta_path.is_file()

    def load(self, force_reload: bool = False) -> MetaInfo:
        """
        Loads metadata from the meta.yaml file into memory.
        If already loaded, returns the cached version unless force_reload is True.

        Args:
            force_reload: If True, bypasses the cache and re-reads from disk.

        Returns:
            The loaded MetaInfo object.

        Raises:
            MetaFileNotFoundError: If the meta file does not exist.
            MetaFormatError: If the file is corrupt or has an invalid format.
        """
        if self._meta_info and not force_reload:
            return self._meta_info

        if not self.exists():
            raise MetaFileNotFoundError(
                f"Meta file not found at '{self.meta_path}'. Cannot load.")
        try:
            with self.meta_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._meta_info = MetaInfo.from_dict(data)
            return self._meta_info
        except (yaml.YAMLError, Exception) as e: # Catch broader exceptions during parsing
            raise MetaFormatError(
                f"Invalid or corrupt meta file '{self.meta_path}': {e}") from e

    def get_meta(self, lazy_load: bool = True) -> Optional[MetaInfo]:
        """
        Gets the metadata object, optionally loading it from disk if not already in memory.

        Args:
            lazy_load: If True, attempts to load from disk if not already loaded.

        Returns:
            The MetaInfo object, or None if it doesn't exist and cannot be loaded.
        """
        if self._meta_info:
            return self._meta_info
        if lazy_load and self.exists():
            try:
                return self.load()
            except MetaError:
                return None # Failed to load, so return None
        return None

    def update(self, **kwargs: Any) -> MetaInfo:
        """
        Updates fields of the metadata object in memory.

        This method supports lazy loading: if metadata is not yet loaded, it will
        be loaded from disk first. The 'updated_at' timestamp is automatically handled.
        This does NOT save changes to disk until `save()` is called.

        Args:
            **kwargs: Key-value pairs of attributes to update.

        Returns:
            The updated MetaInfo object.

        Raises:
            MetaError: If the metadata does not exist and cannot be loaded,
                       or if an invalid field is provided.
        """
        # Step 1: Ensure we have a MetaInfo object to work with (Lazy Loading)
        if not self._meta_info:
            try:
                self.load()
            except MetaFileNotFoundError as e:
                raise MetaError(
                    "Cannot update metadata: No metadata loaded in memory and no "
                    f"file exists at '{self.meta_path}' to load from.") from e

        # We can now be sure self._meta_info is not None.
        current_data = self._meta_info.to_dict()

        # Step 2: Atomically apply the updates
        # This checks for invalid keys before applying anything.
        valid_keys = set(current_data.keys())
        for key in kwargs:
            if key not in valid_keys:
                raise MetaError(f"Invalid metadata field: '{key}'. "
                                f"Valid fields are: {list(valid_keys)}")

        current_data.update(kwargs)

        # Step 3: Recreate the object from the updated data to ensure validation.
        # The 'updated_at' timestamp will be handled by the save() method later.
        self._meta_info = MetaInfo.from_dict(current_data)
        return self._meta_info

    def save(self) -> None:
        """
        Saves the current in-memory metadata to the meta.yaml file.
        Automatically sets the 'updated_at' timestamp.
        """
        if not self._meta_info:
            raise MetaError(
                "No metadata to save. Call create_meta() or load() first.")

        # Automatically update the 'updated_at' timestamp on every save
        self._meta_info.updated_at = datetime.now(timezone.utc)

        self.config_path.mkdir(parents=True, exist_ok=True)
        try:
            with self.meta_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(
                    self._meta_info.to_dict(), f,
                    allow_unicode=True, default_flow_style=False, sort_keys=False
                )
        except IOError as e:
            raise MetaError(
                f"Failed to write to meta file '{self.meta_path}': {e}") from e
