import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import logging


class MetaError(Exception):
    "..."


class MetaFileNotFoundError(MetaError):
    "..."


class MetaFormatError(MetaError):
    "..."


class MetaValidationError(MetaError):
    "..."


class MetaInfo:
    """
    A self-validating data class for configuration metadata.
    It ensures that its instances are always in a valid state.
    """
    # Use class attributes for schema definition
    REQUIRED_FIELDS: Tuple[str, ...] = ('name', 'version')
    OPTIONAL_FIELDS: Dict[str, Any] = {
        'description': '', 'author': '', 'email': '', 'tags': list,
        'category': '', 'dependencies': list, 'notes': '',
        'created_from': '', 'renamed_from': ''
    }

    def __init__(self, **kwargs):
        # Initialize all known fields to their defaults or None
        self._data: Dict[str, Any] = {
            field: default() if callable(default) else default
            for field, default in self.OPTIONAL_FIELDS.items()
        }
        self._data.update({field: None for field in self.REQUIRED_FIELDS})

        # Store custom fields separately
        self._extra_fields: Dict[str, Any] = {}

        # Set initial values, triggering property setters for validation
        all_initial_data = kwargs.copy()
        for key, value in all_initial_data.items():
            # Use setattr to invoke property setters
            setattr(self, key, value)

        # Final validation for required fields
        for field in self.REQUIRED_FIELDS:
            if self._data.get(field) is None:
                raise MetaValidationError(
                    f"Required field '{field}' was not provided.")

        # Set timestamps
        self.created_at = datetime.now()
        self._data['updated_at'] = self.created_at

    @property
    def name(self) -> str:
        return self._data['name']

    @name.setter
    def name(self, value: str):
        if not value or not isinstance(value, str):
            raise MetaValidationError(
                "Field 'name' must be a non-empty string.")
        self._data['name'] = value
        self._touch()

    @property
    def version(self) -> str:
        return self._data['version']

    @version.setter
    def version(self, value: str):
        if not value or not isinstance(value, str):
            raise MetaValidationError(
                "Field 'version' must be a non-empty string.")
        # Basic semver check could go here
        self._data['version'] = value
        self._touch()

    @property
    def tags(self) -> List[str]:
        return self._data['tags']

    @tags.setter
    def tags(self, value: List[str]):
        if not isinstance(value, list):
            raise MetaValidationError("Field 'tags' must be a list.")
        # Store unique, sorted tags
        self._data['tags'] = sorted(list(set(value)))
        self._touch()

    def _touch(self):
        """Updates the 'updated_at' timestamp."""
        self._data['updated_at'] = datetime.now()

    def get(self, field: str, default: Any = None) -> Any:
        """Safely gets a field from standard, optional, or extra fields."""
        if field in self._data:
            return self._data[field]
        return self._extra_fields.get(field, default)

    def set(self, field: str, value: Any):
        """Sets any field, routing to properties or extra fields."""
        if hasattr(self, field):
            setattr(self, field, value)
        else:
            self._extra_fields[field] = value
            self._touch()

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the object to a dictionary for YAML output."""
        # Start with required and optional fields from internal data
        output_dict = {
            key: value for key, value in self._data.items()
            # Include required fields even if "empty"
            if value or key in self.REQUIRED_FIELDS
        }
        # Convert datetime objects to ISO 8601 strings for serialization
        if 'created_at' in output_dict:
            output_dict['created_at'] = output_dict['created_at'].isoformat()
        if 'updated_at' in output_dict:
            output_dict['updated_at'] = output_dict['updated_at'].isoformat()

        # Add non-empty extra fields
        output_dict.update({k: v for k, v in self._extra_fields.items() if v})
        return output_dict

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MetaInfo':
        """Creates an instance from a dictionary, handling string timestamps."""
        if not isinstance(data, dict):
            raise MetaFormatError(
                f"Metadata must be a dictionary, but got {type(data)}.")

        init_data = data.copy()

        # Convert string timestamps back to datetime objects before init if they exist
        created_at_str = init_data.pop('created_at', None)
        # We always generate a new 'updated_at'
        init_data.pop('updated_at', None)

        instance = cls(**init_data)

        # Manually set the created_at from the file to preserve it
        if created_at_str:
            try:
                instance.created_at = datetime.fromisoformat(created_at_str)
            except (ValueError, TypeError):
                logging.warning(
                    f"Invalid 'created_at' format '{created_at_str}'. Ignoring.")

        return instance


class MetaManager:
    """A service to manage the lifecycle (load/save) of a MetaInfo object."""
    META_FILENAME = "meta.yaml"

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.meta_path = self.config_path / self.META_FILENAME
        self._meta_info: Optional[MetaInfo] = None

        if self.meta_path.is_file():
            self.load()

    def exists(self) -> bool:
        """Checks if the meta.yaml file exists on disk."""
        return self.meta_path.is_file()

    def load(self):
        """Loads meta.yaml from disk, creating a MetaInfo object."""
        if not self.exists():
            raise MetaFileNotFoundError(
                f"Meta file not found at '{self.meta_path}'.")
        try:
            with self.meta_path.open('r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            self._meta_info = MetaInfo.from_dict(data)
        except (yaml.YAMLError, MetaError) as e:
            logging.error(
                f"Failed to load or parse meta file '{self.meta_path}': {e}")
            self._meta_info = None  # Ensure invalid state doesn't persist
            raise MetaFormatError(f"Invalid meta file: {e}") from e

    def save(self):
        """Saves the current MetaInfo object to meta.yaml."""
        if self._meta_info is None:
            raise MetaError(
                "No metadata to save. Load or create metadata first.")

        self.config_path.mkdir(parents=True, exist_ok=True)
        try:
            with self.meta_path.open('w', encoding='utf-8') as f:
                yaml.safe_dump(
                    self._meta_info.to_dict(), f,
                    allow_unicode=True, default_flow_style=False, sort_keys=False
                )
        except IOError as e:
            raise MetaError(
                f"Failed to write to meta file '{self.meta_path}': {e}") from e

    def get_meta(self) -> Optional[MetaInfo]:
        """
        Returns the loaded MetaInfo object for manipulation.
        Returns None if no meta file exists or it failed to load.
        """
        return self._meta_info

    def create_meta(self, name: str, version: str = "1.0.0", **kwargs):
        """
        Creates a new MetaInfo object and sets it as the manager's current metadata.
        This does NOT save it to disk until `save()` is called.
        """
        all_data = {'name': name, 'version': version, **kwargs}
        self._meta_info = MetaInfo(**all_data)

    def summary_info(self) -> Dict[str, Any]:
        """
        Returns a summary of the current metadata for display purposes.
        This does not include extra fields.
        """
        if self._meta_info is None:
            return {}

        summary = {
            'name': self._meta_info.name,
            'version': self._meta_info.version,
            'description': self._meta_info.get('description', ''),
            'author': self._meta_info.get('author', ''),
        }
        return summary
