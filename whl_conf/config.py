import filecmp
import logging
import shutil
import tarfile
from pathlib import Path
from typing import Optional, Dict, Any


from whl_conf.manifest import ManifestManager, ManifestError
from whl_conf.meta import MetaManager, MetaError
from whl_conf.confs_lock import attribute_lock, LockError

# Use logging instead of print for library-level code
logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Base exception for configuration manager errors."""
    pass


class ConfigNotFoundError(ConfigError):
    """Exception raised when a configuration is not found."""
    pass


class ConfigAlreadyExistsError(ConfigError):
    """Exception raised when a configuration with the same name already exists."""
    pass


class ConfigRenameError(ConfigError):
    """Exception raised during configuration renaming."""
    pass


class ConfigPermissionError(ConfigError):
    """Exception raised for permission issues during file operations."""
    pass


class ConfigLayoutError(ConfigError):
    """Exception raised for errors related to the layout file."""
    pass


class ConfigManager:
    """
    Manages a collection of configurations in a stateless and robust manner.
    The manager itself is stateless; all operations are performed on configuration
    names passed as arguments, ensuring thread safety and logical consistency.
    """

    def __init__(self, base_dir: str = '.'):
        """
        Initializes the configuration manager.

        Args:
            base_dir: The root directory of the configuration repository.
        """
        self.base_dir = Path(base_dir).resolve()
        self.confs_dir = self.base_dir / 'confs'
        self.current_link_path = self.confs_dir / 'current'
        self._ensure_directories_exist()

    # --- Private Helper Methods ---

    def _ensure_directories_exist(self):
        """Ensures the main confs directory exists."""
        try:
            self.confs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigPermissionError(
                f"Cannot create confs directory {self.confs_dir}: {e}") from e

    def _get_config_path(self, config_name: str) -> Path:
        """Validates a config name and returns its full path."""
        if not config_name or not isinstance(config_name, str) or not config_name.strip():
            raise ValueError("Config name must be a non-empty string.")
        if any(c in config_name for c in r'/\..:*?<>|' + '\x00'):
            raise ValueError(
                f"Config name '{config_name}' contains illegal characters.")
        if config_name.lower() in ['current']:
            raise ValueError(f"'{config_name}' is a reserved name.")
        return self.confs_dir / config_name

    def _config_exists(self, config_name: str) -> bool:
        """Checks if a configuration directory exists and is valid."""
        try:
            return self._get_config_path(config_name).is_dir()
        except ValueError:
            return False

    def _get_meta_manager_for(self, config_name: str) -> MetaManager:
        """Factory for creating a MetaManager for a specific config."""
        config_path = self._get_config_path(config_name)
        return MetaManager(config_path)

    def _get_manifest_manager_for(self, config_name: str) -> ManifestManager:
        """Factory for creating a ManifestManager for a specific config."""
        config_path = self._get_config_path(config_name)
        return ManifestManager(config_path)

    def _get_active_config_name_unlocked(self) -> Optional[str]:
        """Gets the active config name. Assumes a lock is already held."""
        if not self.current_link_path.is_symlink():
            return None
        try:
            target_path = self.current_link_path.resolve()
            # Ensure the link is valid and points within our confs_dir
            if target_path.is_dir() and target_path.parent == self.confs_dir:
                return target_path.name
        except (FileNotFoundError, OSError):
            return None
        return None

    # --- Public API Methods (Locked) ---
    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def get_active_config(self) -> Optional[str]:
        """
        Returns the name of the currently active configuration.
        This is a locked, read-only operation.
        """
        # This public method can be locked if needed, but it's often better to have
        # an unlocked internal version for other methods to use.
        # For simplicity in this case, we'll assume it's fast enough not to require a lock
        # if it's just a read, but for consistency let's lock it.
        return self._get_active_config_name_unlocked()

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def list_configs(self):
        """
        Lists all available configurations, printing them directly to the console.

        - This operation is thread-safe due to the decorator lock.
        - It highlights the currently active configuration with a '*'.
        """
        try:
            # An unlocked helper is called from within the locked method
            active_name = self._get_active_config_name_unlocked()

            # Use a temporary list to hold data for sorting before printing
            configs_to_print = []

            if not self.confs_dir.is_dir():
                print("Configuration directory does not exist.")
                return

            for entry in self.confs_dir.iterdir():
                # We only care about directories, and we must ignore the 'current' symlink
                if entry.is_dir() and not entry.is_symlink():
                    config_name = entry.name
                    description = "N/A"
                    try:
                        # Create a manager for the specific config being inspected
                        meta_mgr = self._get_meta_manager_for(config_name)
                        meta = meta_mgr.get_meta()
                        if meta:
                            description = meta.get(
                                'description', 'No description.')

                    except MetaError as e:
                        # Log the detailed error for debugging purposes
                        logger.warning(
                            f"Could not read metadata for '{config_name}': {e}")
                        description = "[Error reading metadata]"

                    configs_to_print.append({
                        "name": config_name,
                        "description": description,
                    })

            if not configs_to_print:
                print("No configurations found.")
                return

            # Sort the configurations alphabetically by name before printing
            sorted_configs = sorted(configs_to_print, key=lambda x: x['name'])

            print("Available Configurations:")
            print("-------------------------")
            for config in sorted_configs:
                # Add the '*' prefix if the config is the active one
                prefix = "* " if config['name'] == active_name else "  "
                # Use formatted string for clean, aligned output
                print(
                    f"{prefix}{config['name']:<25} | {config['description']}")
            print("-------------------------")
            if active_name:
                print(f"(* = active configuration)")

        except LockError as e:
            # It's good practice for the method to handle its own lock errors
            logger.error(
                f"Failed to list configurations due to a lock error: {e}")
            print(
                "Error: Could not acquire a lock to read configurations. Please try again.")
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while listing configurations: {e}", exc_info=True)
            print("An unexpected error occurred. Check logs for details.")

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def show_info(self, config_name: str) -> Dict[str, Any]:
        """
        Retrieves and prints a summary of information for a given configuration.

        Args:
            config_name (str): The name of the configuration to retrieve info for.

        Returns:
            Dict[str, Any]: A dictionary containing the summary information.
                            Keys are in English.
        Raises:
            ConfigNotFoundError: If the specified configuration does not exist.
        """
        if not self._config_exists(config_name):
            raise ConfigNotFoundError(f"Configuration '{config_name}' not found.")

        config_path = self._get_config_path(config_name)

        # Initialize summary dictionary with common fields
        summary: Dict[str, Any] = {
            "name": config_name,
            "isActive": config_name == self._get_active_config_name_unlocked(),
            "path": str(config_path),
            "sizeKB": 0, # Default to 0, calculate below
        }

        # Calculate directory size
        try:
            summary["sizeKB"] = sum(
                f.stat().st_size for f in config_path.rglob('*') if f.is_file()
            ) / 1024
        except Exception as e:
            logger.warning(f"Could not calculate size for '{config_name}': {e}")
            summary["sizeKB"] = "N/A" # Indicate if size calculation failed

        # Get Meta Info Summary using helper
        meta_mgr = self._get_meta_manager_for(config_name)
        summary["metadata"] = meta_mgr.summary_info()

        # Get Manifest Info Summary using helper
        manifest_mgr = self._get_manifest_manager_for(config_name)
        summary["manifest"] = manifest_mgr.summary_info()

        # Print the summary in a user-friendly English format
        print(f"--- Configuration: ---")
        print(f"  Name: {summary['name']}")
        print(f"  Status: {'Active' if summary['isActive'] else 'Inactive'}")
        print(f"  Path: {summary['path']}")
        print(f"  Size: {summary['sizeKB']:.2f} KB" if isinstance(summary['sizeKB'], (int, float)) else f"  Size: {summary['sizeKB']}")

        print("\n  Metadata:")
        meta_summary = summary['metadata']
        print(f"    Version: {meta_summary.get('version', 'N/A')}")
        print(f"    Description: {meta_summary.get('description', 'N/A')}")
        print(f"    Author: {meta_summary.get('author', 'N/A')}")
        print(f"    Created At: {meta_summary.get('createdAt', 'N/A')}")
        print(f"    Updated At: {meta_summary.get('updatedAt', 'N/A')}")

        print("\n  Manifest:")
        manifest_summary = summary['manifest']
        print(f"    Files Configured: {manifest_summary['filesCount']}")
        return summary

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def create_config(self, template_name: str, new_config_name: str):
        """Creates a new configuration from a template."""
        if not self._config_exists(template_name):
            raise ConfigNotFoundError(
                f"Template configuration '{template_name}' not found.")
        if self._config_exists(new_config_name):
            raise ConfigAlreadyExistsError(
                f"Configuration '{new_config_name}' already exists.")

        template_path = self._get_config_path(template_name)
        new_config_path = self._get_config_path(new_config_name)

        try:
            shutil.copytree(template_path, new_config_path)

            # Atomically update the new meta file
            meta_mgr = self._get_meta_manager_for(new_config_name)
            meta_mgr.load()  # Load the copied meta info
            meta_mgr.update(name=new_config_name, created_from=template_name)
            meta_mgr.save()  # Save changes
        except (OSError, MetaError) as e:
            # Atomic cleanup
            if new_config_path.exists():
                shutil.rmtree(new_config_path, ignore_errors=True)
            raise ConfigError(
                f"Failed to create config '{new_config_name}': {e}") from e

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def delete_config(self, config_name: str):
        """Deletes a configuration."""
        if not self._config_exists(config_name):
            raise ConfigNotFoundError(
                f"Configuration '{config_name}' not found.")
        if config_name == self._get_active_config_name_unlocked():
            raise ConfigError(
                "Cannot delete the currently active configuration.")

        config_path = self._get_config_path(config_name)
        try:
            shutil.rmtree(config_path)
        except OSError as e:
            raise ConfigPermissionError(
                f"Failed to delete '{config_name}': {e}") from e

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def activate_config(self, config_name: str):
        """Activates a configuration, deactivating the previous one."""
        if not self._config_exists(config_name):
            raise ConfigNotFoundError(
                f"Configuration '{config_name}' not found.")

        # Step 1: Deactivate the current configuration
        current_active = self._get_active_config_name_unlocked()
        if current_active:
            if current_active == config_name:
                logger.info(
                    f"Configuration '{config_name}' is already active.")
                return  # Nothing to do
            logger.info(
                f"Deactivating current configuration: '{current_active}'")
            self._deactivate_unlocked(current_active)

        # Step 2: Activate the new configuration
        logger.info(f"Activating new configuration: '{config_name}'")
        self._activate_unlocked(config_name)

    # These helpers are not locked, as they are part of the `activate_config` unit of work.
    def _deactivate_unlocked(self, config_name: str):
        """Internal logic to remove symlinks for a configuration."""
        try:
            manifest_mgr = self._get_manifest_manager_for(config_name)
            if not manifest_mgr.exists():
                return
            manifest_mgr.load()
            for mapping in manifest_mgr.file_mappings:
                dest_path = Path(mapping.dest)
                if dest_path.is_symlink():
                    dest_path.unlink()
        except (ManifestError, OSError) as e:
            logger.error(
                f"Error during deactivation of '{config_name}': {e}", exc_info=True)
            raise ConfigError(
                f"Failed to cleanly deactivate '{config_name}'.") from e

    def _activate_unlocked(self, config_name: str):
        """Internal logic to create symlinks for a configuration."""
        config_path = self._get_config_path(config_name)
        manifest_mgr = self._get_manifest_manager_for(config_name)
        if not manifest_mgr.exists():
            raise ConfigError(
                f"Cannot activate '{config_name}': manifest.yaml not found.")

        manifest_mgr.load()
        if manifest_mgr.validate_files_exist():
            raise ConfigError(
                f"Cannot activate '{config_name}': one or more source files are missing.")

        try:
            # Create file symlinks
            for mapping in manifest_mgr.file_mappings:
                src_path = config_path / mapping.src
                dest_path = Path(mapping.dest)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                if dest_path.exists() or dest_path.is_symlink():
                    dest_path.unlink()  # Ensure destination is clean
                dest_path.symlink_to(src_path.resolve())

            # Update the 'current' symlink
            if self.current_link_path.exists() or self.current_link_path.is_symlink():
                self.current_link_path.unlink()
            self.current_link_path.symlink_to(config_path)
        except (ManifestError, OSError) as e:
            logger.error(
                f"Error during activation of '{config_name}': {e}", exc_info=True)
            # Attempt a rollback
            self._deactivate_unlocked(config_name)
            raise ConfigError(
                f"Failed to activate '{config_name}'. System may be in an inconsistent state.") from e


    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def diff_configs(self, config1_name: str, config2_name: str) -> Dict[str, Any]:
        """
        Compares two configurations and returns a structured dictionary of the differences.

        This method is designed to provide data that a UI layer can format and display.
        """
        if not self._config_exists(config1_name):
            raise ConfigNotFoundError(
                f"Configuration '{config1_name}' not found.")
        if not self._config_exists(config2_name):
            raise ConfigNotFoundError(
                f"Configuration '{config2_name}' not found.")

        config1_path = self._get_config_path(config1_name)
        config2_path = self._get_config_path(config2_name)

        mgr1 = self._get_manifest_manager_for(config1_name)
        mgr2 = self._get_manifest_manager_for(config2_name)

        # Load manifests, handling cases where they might not exist
        if mgr1.exists():
            mgr1.load()
        if mgr2.exists():
            mgr2.load()

        comparison = mgr1.compare_with(mgr2)

        # Now, check file content for common files
        content_diffs = []
        for src in comparison['common_files']:
            file1 = config1_path / src
            file2 = config2_path / src
            # Ensure both files exist before comparing
            if file1.is_file() and file2.is_file():
                if not filecmp.cmp(file1, file2, shallow=False):
                    content_diffs.append(src)

        comparison['content_different_files'] = content_diffs

        return {
            'config1': config1_name,
            'config2': config2_name,
            'comparison': comparison
        }

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def rename_config(self, old_name: str, new_name: str):
        """
        Renames a configuration, including its directory and internal metadata.
        Also updates the 'current' symlink if the renamed config was active.
        """
        if not self._config_exists(old_name):
            raise ConfigNotFoundError(
                f"Configuration to rename, '{old_name}', not found.")

        # Validate the new name before any operations
        # This will raise ValueError on invalid names
        new_path = self._get_config_path(new_name)
        if self._config_exists(new_name):
            raise ConfigAlreadyExistsError(
                f"Target configuration name '{new_name}' already exists.")

        old_path = self._get_config_path(old_name)
        is_active = (old_name == self._get_active_config_name_unlocked())

        try:
            # 1. Rename the directory
            old_path.rename(new_path)

            # 2. Update the meta.yaml file inside the newly named directory
            meta_mgr = self._get_meta_manager_for(new_name)
            if meta_mgr.exists():
                meta_mgr.load()
                meta_mgr.update(name=new_name, renamed_from=old_name)
                meta_mgr.save()

            # 3. If it was active, update the 'current' symlink to point to the new path
            if is_active:
                if self.current_link_path.exists() or self.current_link_path.is_symlink():
                    self.current_link_path.unlink()
                self.current_link_path.symlink_to(new_path)

        except (OSError, MetaError) as e:
            # Attempt to roll back the rename if it failed midway
            if new_path.exists() and not old_path.exists():
                new_path.rename(old_path)
            raise ConfigError(
                f"Failed to rename '{old_name}' to '{new_name}': {e}") from e

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def export_config(self, config_name: str, archive_path_str: str) -> Path:
        """
        Exports a specified configuration to a .tar.gz archive.

        Returns:
            The final Path object of the created archive.

        Raises:
            ConfigError if the config is missing files.
        """
        if not self._config_exists(config_name):
            raise ConfigNotFoundError(
                f"Configuration to export, '{config_name}', not found.")

        config_path = self._get_config_path(config_name)
        archive_path = Path(archive_path_str)

        # Ensure correct suffix
        if not archive_path.name.endswith('.tar.gz'):
            archive_path = archive_path.with_suffix('.tar.gz')

        # Ensure parent directory exists
        archive_path.parent.mkdir(parents=True, exist_ok=True)

        # Validate configuration integrity before exporting
        manifest_mgr = self._get_manifest_manager_for(config_name)
        if manifest_mgr.exists():
            manifest_mgr.load()
            missing_files = manifest_mgr.validate_files_exist()
            if missing_files:
                # The caller (CLI) can catch this and prompt the user if desired
                raise ConfigError(
                    f"Export failed: Config '{config_name}' is incomplete. "
                    f"Missing files: {', '.join(missing_files)}"
                )

        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                # arcname ensures the files are inside a directory named after the config
                tar.add(config_path, arcname=config_path.name)

            logger.info(
                f"Successfully exported '{config_name}' to '{archive_path}'")
            return archive_path
        except (tarfile.TarError, OSError) as e:
            raise ConfigError(
                f"Failed to create archive for '{config_name}': {e}") from e

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def import_config(self, archive_path_str: str, overwrite: bool = False) -> str:
        """
        Imports a configuration from a .tar.gz archive.

        Args:
            archive_path_str: Path to the .tar.gz file.
            overwrite (bool): If True, overwrite an existing configuration with the same name.
                              If False, an exception is raised if the config exists.

        Returns:
            The name of the imported configuration.
        """
        archive_path = Path(archive_path_str)
        if not archive_path.is_file():
            raise FileNotFoundError(
                f"Archive file not found at '{archive_path}'")

        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                members = tar.getnames()
                if not members:
                    raise ConfigError("Archive is empty.")

                # The config name is the top-level directory in the archive
                config_name = Path(members[0]).parts[0]
                self._get_config_path(config_name)  # Validates the name

                target_path = self._get_config_path(config_name)

                if target_path.exists():
                    if not overwrite:
                        raise ConfigAlreadyExistsError(
                            f"Configuration '{config_name}' already exists. "
                            "Use overwrite option to replace it."
                        )
                    logger.info(
                        f"Overwriting existing configuration '{config_name}'")
                    shutil.rmtree(target_path)

                # Extract the archive into the `confs` directory
                tar.extractall(path=self.confs_dir)
                logger.info(
                    f"Successfully imported and extracted '{config_name}'.")

                return config_name

        except (tarfile.TarError, OSError, ValueError) as e:
            raise ConfigError(
                f"Failed to import from archive '{archive_path}': {e}") from e
