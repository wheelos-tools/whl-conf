import logging
import shutil
import uuid
from datetime import datetime, timezone
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Set
import urllib.request
import zipfile
import shutil
import tempfile

from whl_conf.meta import MetaManager, MetaError
from whl_conf.confs_lock import attribute_lock, LockError
from whl_conf.config_compare import ConfigComparator

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


class ConfigActiveError(ConfigError):
    """Exception raised for issues with the active configuration."""
    pass


class ManifestNotFoundError(ConfigError):
    """Exception raised when a manifest file is not found."""
    pass

class PathNotInConfigError(ConfigError):
    """Exception raised when an operation is forbidden."""
    pass


class ConfigManager:
    """
    Manages a collection of configurations in a stateless and robust manner.
    The manager itself is stateless; all operations are performed on configuration
    names passed as arguments, ensuring thread safety and logical consistency.
    """

    MANIFEST_FILE = ".config.manifest"
    CONF_DIR = "data/confs"

    def __init__(self, base_dir: str):
        """
        Initializes the configuration manager.

        Args:
            base_dir: The root directory of the configuration repository.
        """
        self.base_dir = Path(base_dir).resolve()
        self.confs_dir = self.base_dir / self.CONF_DIR
        self.manifest_path = self.confs_dir / self.MANIFEST_FILE
        self.current_link_path = self.confs_dir / 'current'

    # --- Private Helper Methods ---

    def _get_config_path(self, config_name: str) -> Path:
        """Validates a config name and returns its full path."""
        if not config_name or not isinstance(config_name, str) or not config_name.strip():
            raise ValueError("Config name must be a non-empty string.")
        if any(c in config_name for c in r'/\..:*?<>|' + '\x00'):
            raise ValueError(
                f"Config name '{config_name}' contains illegal characters.")
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

    def _read_manifest(self) -> List[Path]:
        """Safely read and parse manifest files."""
        if not self.manifest_path.is_file():
            return []
        try:
            with self.manifest_path.open('r') as f:
                data = json.load(f)
            return [Path(p) for p in data]
        except (json.JSONDecodeError, TypeError):
            logging.warning(
                "Manifest file is corrupted or improperly formatted. Treating as empty manifest.")
            return []

    def _write_manifest(self, relative_paths: List[Path]):
        """Atomically write the manifest file."""
        paths_as_str = [str(p) for p in relative_paths]
        temp_path = self.manifest_path.with_suffix(
            f".tmp-{os.urandom(4).hex()}")
        with temp_path.open('w') as f:
            json.dump(paths_as_str, f, indent=2)
        os.rename(temp_path, self.manifest_path)

    def _get_all_configs(self) -> list[dict]:
        """
        Retrieves a list of all available configurations with their metadata.

        This method is the core data-gathering logic, designed for reusability.
        It is not directly locked; locking should be handled by the calling method.

        Returns:
            A list of dictionaries, where each dictionary represents a configuration.
            Example: [{'name': 'config1', 'description': 'Desc1', 'is_active': False}]
        """
        try:
            active_name = self._get_active_config_name_unlocked()
        except Exception:
            active_name = None

        configs_data = []

        for conf_path in self.confs_dir.glob("*/"):
            if not conf_path.is_dir() or conf_path.is_symlink():
                continue

            config_name = conf_path.name
            description = "N/A"

            try:
                meta_mgr = MetaManager(conf_path)
                if meta_mgr.exists():
                    meta = meta_mgr.get_meta()
                    if meta:
                        description = meta.to_dict().get(
                            'description', 'No description.')

            except MetaError as e:
                logging.warning(
                    f"Could not read or parse metadata for '{config_name}': {e}")
                description = "[Error reading metadata]"

            configs_data.append({
                "name": config_name,
                "description": description,
                "is_active": config_name == active_name,
            })
        return sorted(configs_data, key=lambda x: x['name'])

    def _get_config_details(self, config_name: str) -> dict:
        """
        Gathers comprehensive details for a specific configuration.
        (This function is already well-structured and remains unchanged)

        Args:
            config_name: The name of the configuration.

        Returns:
            A dictionary containing detailed configuration information.

        Raises:
            ConfigNotFoundError: If the configuration does not exist.
        """
        if not self._config_exists(config_name):
            raise ConfigNotFoundError(
                f"Configuration '{config_name}' not found.")

        config_path = self._get_config_path(config_name)

        # 1. Calculate directory size with robust error handling
        size_kb = "N/A"
        try:
            total_size_bytes = sum(
                f.stat().st_size for f in config_path.rglob('*') if f.is_file())
            size_kb = total_size_bytes / 1024
        except Exception as e:
            logging.warning(
                f"Could not calculate directory size for '{config_name}': {e}")

        # 2. Load full metadata using MetaManager and get the MetaInfo object
        meta_mgr = self._get_meta_manager_for(config_name)
        # We now need the object itself, not its dict form yet
        meta_info_object = meta_mgr.get_meta()

        # 3. Assemble the final structured data
        return {
            "name": config_name,
            "isActive": config_name == self._get_active_config_name_unlocked(),
            "path": str(config_path),
            "sizeKB": size_kb,
            "meta_object": meta_info_object,
        }

    def _deactivate_current_unlocked(self):
        """
        Safely deactivates the current configuration by removing all links
        listed in the manifest file.
        """
        manifest_entries = self._read_manifest()
        if not manifest_entries:
            logging.info(
                "No manifest found or manifest is empty. Nothing to deactivate.")
            return

        logging.info("Deactivating current configuration based on manifest...")
        # Iterate in reverse to handle nested files before their parent directories
        for rel_path in reversed(manifest_entries):
            link_path = self.base_dir / rel_path

            # Only remove what we expect to be a symlink managed by us.
            if link_path.is_symlink():
                try:
                    # Defensive check: ensure the link points somewhere inside our configs root
                    if self.confs_dir in link_path.resolve(strict=True).parents:
                        link_path.unlink()
                        # Attempt to remove the parent directory if it's now empty
                        try:
                            link_path.parent.rmdir()
                        except OSError:
                            pass  # Directory not empty, which is fine.
                except FileNotFoundError:
                    pass  # Link is broken, ignore.
            elif not link_path.exists():
                pass  # Link already gone, ignore.
            else:
                logging.warning(
                    f"Skipping cleanup of '{link_path}' because it is a real file, not a symlink.")

        # Clean up the manifest and the 'current' symlink itself
        self.manifest_path.unlink(missing_ok=True)
        self.current_link_path.unlink(missing_ok=True)
        logging.info("Deactivation complete.")

    def _update_current_link_unlocked(self, target_path: Path):
        """Atomically updates the 'current' symlink to point to the target path."""
        # Create a temporary link first
        temp_link = self.current_link_path.with_suffix(
            f'.tmp-{os.urandom(4).hex()}')

        # Point the temporary link to the new target directory
        temp_link.symlink_to(target_path.resolve())

        # Atomically rename the temp link to the final name, overwriting the old one.
        os.rename(temp_link, self.current_link_path)

    def _rollback_creation(self, paths_to_delete: List[Path]):
        """Rolls back a failed activation by deleting all paths created."""
        for rel_path in reversed(paths_to_delete):
            link_path = self.base_dir / rel_path
            if link_path.is_symlink():
                link_path.unlink(missing_ok=True)
                try:
                    link_path.parent.rmdir()
                except OSError:
                    pass  # Not empty, ignore.

    def _resolve_source_paths_to_files(self, source_paths: List[str]) -> Set[Path]:
        """Helper to recursively find all files given a list of paths."""
        all_files: Set[Path] = set()
        for path_str in source_paths:
            source_path = Path(path_str).resolve()
            if not source_path.exists():
                raise FileNotFoundError(
                    f"Source path '{path_str}' does not exist.")

            if source_path.is_file():
                all_files.add(source_path)
            elif source_path.is_dir():
                for item in source_path.rglob('*'):
                    if item.is_file():
                        all_files.add(item)
        return all_files


    # --- Public API Methods (Locked) ---
    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def list_configs(self):
        """
        Lists all available configurations, printing them directly to the console.

        This is the user-facing method that handles locking and console output.
        """
        try:
            all_configs = self._get_all_configs()
            if not all_configs:
                print("No configurations found.")
                return

            print("Available Configurations:")
            print(f"{'':<2} {'Name':<25} | {'Description'}")
            print("-" * 45)

            has_active = False
            for config in all_configs:
                prefix = "* " if config['is_active'] else "  "
                if config['is_active']:
                    has_active = True

                print(
                    f"{prefix}{config['name']:<25} | {config['description']}")

            print("-" * 45)
            if has_active:
                print("(* = active configuration)")

        except LockError as e:
            logging.error(
                f"Failed to list configurations due to a lock error: {e}")
            print(f"\n[Error] Could not acquire lock. Please try again.")
        except Exception as e:
            logging.error(
                f"An unexpected error occurred while listing configurations: {e}", exc_info=True)
            print(
                f"\n[Error] An unexpected error occurred. Check logs for details.")

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def show_config(self, config_name: str) -> None:
        """
        Retrieves and prints a detailed summary for a given configuration.

        This method now delegates the complex task of metadata formatting
        to the MetaInfo object's `pretty_print` method, simplifying its own logic.

        Args:
            config_name: The name of the configuration to display.
        """
        try:
            # Step 1: Gather all the necessary information and objects
            details = self._get_config_details(config_name)
            meta_info = details.get("meta_object")

            # --- Presentation Logic ---
            print(f"Configuration Details: [{details['name']}]")
            print("-" * 40)

            # Step 2: Print the basic, non-metadata information
            print(
                f"  Status : {'Active' if details['isActive'] else 'Inactive'}")
            print(f"  Path   : {details['path']}")
            if isinstance(details['sizeKB'], (int, float)):
                print(f"  Size   : {details['sizeKB']:.2f} KB")
            else:
                print(f"  Size   : {details['sizeKB']}")

            # Step 3: Delegate metadata printing to the meta object
            # The MetaInfo object now handles its own rich representation.
            if meta_info:
                meta_info.pretty_print()
            else:
                print("\n  Metadata:")
                print("    No metadata found (meta.yaml might be missing or empty).")

            print("-" * 40)

        except ConfigNotFoundError as e:
            print(f"[Error] {e}")
        except Exception as e:
            logging.error(
                f"An unexpected error occurred in show_info for '{config_name}': {e}",
                exc_info=True
            )
            print("[Error] An unexpected error occurred. See logs for details.")

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def create_config(self, template_name: str, new_config_name: str):
        """Creates a new configuration from a template."""
        if new_config_name in ['current']:
            raise ValueError(f"'current' is a reserved name.")
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
            meta_mgr.update(
                config_id=str(uuid.uuid4()),
                created_at=datetime.now(timezone.utc),
                description=f"Created from template '{template_name}'"
            )
            meta_mgr.save()
        except (OSError, MetaError) as e:
            # Atomic cleanup
            if new_config_path.exists():
                shutil.rmtree(new_config_path, ignore_errors=True)
            raise ConfigError(
                f"Failed to create config '{new_config_name}': {e}") from e

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def delete_config(self, config_name: str):
        """Deletes a configuration."""
        if config_name in ['current']:
            raise ValueError(f"'current' is a reserved name.")
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
    def activate_config(self, config_name: str, dry_run: bool = False) -> None:
        """
        Activates a specified configuration using a manifest-driven,
        transactional approach for maximum safety.

        Args:
            config_name: The name of the configuration to activate.
            dry_run: If True, prints the actions that would be taken without
                     making any changes to the file system.
        """
        if config_name in ['current']:
            raise ValueError(f"'current' is a reserved name.")
        source_config_path = self._get_config_path(config_name)
        if not source_config_path.is_dir():
            raise ConfigNotFoundError(
                f"Configuration '{config_name}' not found or is not a directory.")

        active_config_name = self._get_active_config_name_unlocked()

        # Idempotency check
        if dry_run:
            print(f"--- Dry Run: Activating '{config_name}' ---")

        # --- Phase 1: Plan deactivation ---
        # No actual deactivation happens in dry_run, we just report it
        if active_config_name:
            if dry_run:
                print(
                    f"[DRY RUN] Would deactivate the current configuration: '{active_config_name}'")
            else:
                logging.info(
                    f"Deactivating current configuration: '{active_config_name}'")
                self._deactivate_current_unlocked()
        elif not active_config_name and dry_run:
            print("[DRY RUN] No active configuration to deactivate.")

        # --- Phase 2: Plan new configuration links ---
        actions_to_perform = []
        for item in source_config_path.rglob('*'):
            if not item.is_file() or item.name == "meta.yaml":
                continue

            relative_path = item.relative_to(source_config_path)
            link_path = self.base_dir / relative_path

            action = "CREATE"
            if link_path.is_symlink():
                action = "REPLACE"
            elif link_path.exists():
                action = "OVERWRITE_FILE"

            actions_to_perform.append(
                {'action': action, 'link': link_path, 'target': item})

        if dry_run:
            print(
                f"[DRY RUN] The following {len(actions_to_perform)} links would be managed:")
            for act in actions_to_perform:
                print(
                    f"  - {act['action']:<15} '{act['link']}' -> '{act['target']}'")
            print("--- End of Dry Run ---")
            return

        # --- Phase 3: Execute Activation (only if not a dry run) ---
        logging.info(f"Activating configuration: '{config_name}'")
        newly_created_paths: List[Path] = []
        try:
            for act in actions_to_perform:
                link_path = act['link']
                item = act['target']
                relative_path = item.relative_to(source_config_path)
                newly_created_paths.append(relative_path)

                link_path.parent.mkdir(parents=True, exist_ok=True)
                if link_path.exists() or link_path.is_symlink():
                    if link_path.is_dir() and not link_path.is_symlink():
                        shutil.rmtree(link_path)
                    else:
                        link_path.unlink()
                link_path.symlink_to(item.resolve())

            # Commit the new state
            self._write_manifest(newly_created_paths)
            self._update_current_link_unlocked(source_config_path)
            print(f"Successfully activated configuration '{config_name}'.")
            logging.info(
                f"Successfully activated configuration '{config_name}'.")
        except Exception as e:
            logging.error(
                f"Failed to activate configuration '{config_name}': {e}", exc_info=True)
            self._rollback_creation(newly_created_paths)
            raise ConfigError(
                f"Activation failed and has been rolled back.") from e

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def diff_configs(self, config1_name: str, config2_name: str) -> Dict[str, Any]:
        """
        Compares two configurations using a dedicated comparator and returns a structured diff.

        The output is cleanly categorized into 'added', 'removed', 'modified',
        and 'unmodified' files, making it easy for a UI or script to consume.

        Args:
            config1_name: The name of the base configuration.
            config2_name: The name of the configuration to compare against the base.

        Returns:
            A dictionary containing the comparison results.
        """
        if config1_name == config2_name:
            raise ConfigError("Cannot compare a configuration with itself.")

        # --- 1. Validation and Path Retrieval ---
        path1 = self._get_config_path(config1_name)
        path2 = self._get_config_path(config2_name)

        # --- 2. Delegation to Comparator ---
        comparator = ConfigComparator(path1, path2)
        comparison_result = comparator.compare()

        # --- 3. Assemble the final data structure ---
        result_data = {
            'config1': config1_name,
            'config2': config2_name,
            'comparison': comparison_result
        }

        # --- 4. Delegation to Presenter (for console output) ---
        print(comparator.format_report())

        return result_data

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def rename_config(self, old_name: str, new_name: str) -> None:
        """
        Renames a configuration directory based on a safe workflow.

        This operation is intentionally prohibited if the configuration to be
        renamed is currently active, forcing a "deactivate first" workflow
        to ensure runtime stability.

        Args:
            old_name: The current name of the configuration.
            new_name: The desired new name for the configuration.

        Raises:
            ConfigNotFoundError: If the configuration `old_name` does not exist.
            ConfigAlreadyExistsError: If a configuration with `new_name` already exists.
            OperationForbiddenError: If attempting to rename the currently active configuration.
            ConfigError: For other generic or OS-level errors during the rename.
        """
        if old_name in ['current'] or new_name in ['current']:
            raise ValueError(f"'current' is a reserved name.")
        # 1. If 'a' (old_name) does not exist, exit.
        old_path = self._get_config_path(old_name)
        if not old_path or not old_path.is_dir():
            raise ConfigNotFoundError(
                f"Configuration to rename, '{old_name}', not found.")

        # 2. If 'b' (new_name) already exists, exit.
        # This also validates the new_name format implicitly.
        new_path = self._get_config_path(new_name)
        if new_path.exists():
            raise ConfigAlreadyExistsError(
                f"Target configuration name '{new_name}' already exists.")

        # 3. If 'a' is the active configuration, exit.
        active_config_name = self._get_active_config_name_unlocked()
        if active_config_name == old_name:
            raise ConfigError(
                f"Cannot rename '{old_name}' because it is the currently active configuration. "
                "Please deactivate it first.")

        # If all checks pass, the operation is simple and safe.
        try:
            # 4. Rename the directory 'a' to 'b'.
            old_path.rename(new_path)
            logging.info(
                f"Successfully renamed configuration '{old_name}' to '{new_name}'.")
        except OSError as e:
            # The rename operation is generally atomic on POSIX systems,
            # so a complex rollback is rarely needed. We catch OS errors
            # and wrap them in our custom exception type.
            logging.error(f"An OS error occurred during rename: {e}")
            raise ConfigError(
                f"Failed to rename '{old_name}' to '{new_name}'.") from e


    @attribute_lock(attr_name='confs_dir', timeout=60.0)
    def pull_config(self, config_name: str) -> None:
        """
        Downloads a configuration from a URL, unpacks it, and installs it.

        This method downloads a .zip archive from the given URL to a temporary
        location, unpacks it, and moves it to the configurations directory.
        The operation is designed to be atomic; it will clean up after itself
        if any step fails.

        Args:
            config_name: The name to assign to the new configuration.
            url: The URL of the .zip archive to download.

        Raises:
            ConfigAlreadyExistsError: If a configuration with the target name already exists.
            ConfigError: For network errors, file errors, or if the zip archive
                        is invalid.
        """
        url = "https://github.com/wheelos/wheel.os/releases/download/v1.0.0/{}.zip".format(
            config_name)
        logging.info(
            f"Attempting to pull configuration '{config_name}' from {url}")
        target_path = self._get_config_path(config_name)
        if self._config_exists(config_name):
            raise ConfigAlreadyExistsError(
                f"Cannot pull configuration: '{config_name}' already exists.")

        # Use a temporary directory to handle all artifacts.
        # This ensures everything is cleaned up automatically on exit or failure.
        with tempfile.TemporaryDirectory(suffix="_conf_pull") as temp_dir:
            temp_dir_path = Path(temp_dir)
            zip_path = temp_dir_path / "download.zip"

            # 1. Download the file
            try:
                logging.info(f"Downloading from {url}")
                # Use a proper user-agent to avoid being blocked by some servers.
                req = urllib.request.Request(
                    url, headers={'User-Agent': 'whl-conf-cli/1.0'})
                with urllib.request.urlopen(req) as response, open(zip_path, 'wb') as out_file:
                    shutil.copyfileobj(response, out_file)
            except urllib.error.URLError as e:
                raise ConfigError(
                    f"Network error while downloading from {url}: {e}") from e
            except Exception as e:
                raise ConfigError(f"Failed to download file: {e}") from e

            # 2. Unpack the zip file
            unpacked_root = temp_dir_path / "unpacked"
            try:
                logging.info(f"Unpacking archive: {zip_path}")
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(unpacked_root)
            except zipfile.BadZipFile as e:
                raise ConfigError(
                    f"Downloaded file from {url} is not a valid zip archive.") from e

            # 3. Find the actual content directory within the unpacked archive.
            # Often, zip files contain a single root folder.
            # e.g., template.zip -> ./template/all-the-files
            unpacked_items = list(unpacked_root.iterdir())
            if len(unpacked_items) == 1 and unpacked_items[0].is_dir():
                source_dir = unpacked_items[0]
                logging.debug(
                    f"Zip contains a single root directory: '{source_dir.name}'")
            else:
                source_dir = unpacked_root
                logging.debug("Zip contents will be used directly from the root.")

            # 4. Move the final, unpacked directory to the target location.
            # This is the final atomic operation.
            try:
                shutil.move(str(source_dir), str(target_path))
                logging.info(
                    f"Successfully pulled and installed configuration '{config_name}'.")
            except OSError as e:
                raise ConfigError(
                    f"Failed to move unpacked configuration to final destination: {e}") from e

    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def add_active_config(self, source_paths: List[str], dry_run: bool = False):
        """
        Adds files or directories to the active configuration.

        This method follows a copy-then-link approach:
        1. Copies the source file into the active config's directory, creating a snapshot.
        2. Creates a symlink in the base directory that points to the copied file.
        This operation is transactional and will forcefully overwrite existing files or
        links at the target destination.

        Args:
            source_paths: A list of source file or directory paths to add.
            dry_run: If True, only print the actions that would be taken.
        """
        active_config_name = self._get_active_config_name_unlocked()
        if not active_config_name:
            raise ConfigActiveError("Cannot add: No configuration is currently active.")

        active_config_path = self._get_config_path(active_config_name)

        if dry_run:
            print(f"--- Dry Run: Adding to active config '{active_config_name}' ---")

        # --- Phase 1: Planning and Validation ---
        actions_to_perform = []
        current_manifest_paths = {p for p in self._read_manifest()}
        all_source_files = self._resolve_source_paths_to_files(source_paths)

        for source_file in all_source_files:
            try:
                relative_path = source_file.relative_to(self.base_dir)
            except ValueError:
                raise ValueError(f"Source path '{source_file}' must be inside the base directory '{self.base_dir}'.")

            if relative_path in current_manifest_paths:
                logging.info(f"Path '{relative_path}' is already in the manifest. Skipping.")
                continue

            # Define the two-step paths
            copy_destination = active_config_path / relative_path
            link_path = self.base_dir / relative_path

            actions_to_perform.append({
                'source': source_file,
                'copy_dest': copy_destination,
                'link': link_path,
                'relative': relative_path
            })

        if not actions_to_perform:
            print("No new files to add. All specified paths are already in the manifest.")
            return

        if dry_run:
            print(f"The following {len(actions_to_perform)} actions would be performed:")
            for act in actions_to_perform:
                print(f"  - COPY '{act['source']}'\n    -> TO '{act['copy_dest']}'")
                print(f"  - LINK '{act['link']}'\n    -> TO '{act['copy_dest']}'")
            print("--- End of Dry Run ---")
            return

        # --- Phase 2: Execution with Enhanced Rollback ---
        newly_created_copies: List[Path] = []
        newly_created_links: List[Path] = []
        try:
            logging.info(f"Adding {len(actions_to_perform)} new file(s) to '{active_config_name}'.")

            for act in actions_to_perform:
                copy_dest = act['copy_dest']
                link = act['link']

                # Step 1: Copy file into the active config directory
                copy_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(act['source'], copy_dest) # copy2 preserves metadata
                newly_created_copies.append(copy_dest)

                # Step 2: Create the symlink, forcefully overwriting any existing target
                link.parent.mkdir(parents=True, exist_ok=True)

                # Forcefully remove whatever is at the destination path
                if link.is_symlink():
                    link.unlink()
                elif link.is_file():
                    link.unlink()
                elif link.is_dir():
                    shutil.rmtree(link)

                link.symlink_to(copy_dest)
                newly_created_links.append(link)

            # --- Phase 3: Commit ---
            final_manifest_paths = current_manifest_paths.union({act['relative'] for act in actions_to_perform})
            self._write_manifest(list(final_manifest_paths))

            print(f"Successfully added files to active configuration '{active_config_name}'.")
            logging.info("Add operation complete and manifest updated.")

        except Exception as e:
            logging.error(f"Failed to add to configuration: {e}", exc_info=True)
            print("Error occurred. Rolling back changes...")

            # Rollback in reverse order of creation
            for lnk in reversed(newly_created_links):
                lnk.unlink(missing_ok=True)
            for cpy in reversed(newly_created_copies):
                cpy.unlink(missing_ok=True)

            # An additional step could be to clean up empty directories created
            logging.info("Rollback complete.")
            raise ConfigError("Add operation failed and has been rolled back.") from e


    @attribute_lock(attr_name='confs_dir', timeout=10.0)
    def remove_active_config(self, paths_to_remove: List[str], dry_run: bool = False):
        """
        Removes files or directories from the active configuration. This is the
        mirror operation of `add_active_config`.

        It performs a two-step deletion for each managed path:
        1. Deletes the symlink from the base directory.
        2. Deletes the corresponding file snapshot from within the active config directory.
        The operation is transactional and updates the manifest only upon success.

        Args:
            paths_to_remove: A list of file or directory paths whose managed links should be removed.
            dry_run: If True, only print the actions that would be taken.
        """
        active_config_name = self._get_active_config_name_unlocked()
        if not active_config_name:
            raise ConfigActiveError("Cannot remove: No configuration is currently active.")

        active_config_path = self._get_config_path(active_config_name)

        if dry_run:
            print(f"--- Dry Run: Removing paths from active config '{active_config_name}' ---")

        # --- Phase 1: Planning and Validation ---
        current_manifest = self._read_manifest()
        if not current_manifest:
            print("Manifest is empty. Nothing to remove.")
            return

        relative_paths_to_remove_spec: Set[Path] = set()
        for p_str in paths_to_remove:
            abs_path = Path(p_str).resolve()
            try:
                relative_paths_to_remove_spec.add(abs_path.relative_to(self.base_dir))
            except ValueError:
                raise ValueError(f"Path '{p_str}' is not inside the base directory '{self.base_dir}'.")

        manifest_entries_to_delete: Set[Path] = set()
        for spec_path in relative_paths_to_remove_spec:
            for manifest_path in current_manifest:
                if manifest_path == spec_path or spec_path in manifest_path.parents:
                    manifest_entries_to_delete.add(manifest_path)

        if not manifest_entries_to_delete:
            # Use a more specific exception for better error handling
            raise PathNotInConfigError(
                f"None of the specified paths {paths_to_remove} are managed by the active configuration manifest.")

        if dry_run:
            print(f"The following {len(manifest_entries_to_delete)} items would be removed:")
            for rel_path in sorted(list(manifest_entries_to_delete)):
                print(f"  - UNLINK      '{self.base_dir / rel_path}'")
                print(f"  - DELETE_COPY '{active_config_path / rel_path}'")
            print("--- End of Dry Run ---")
            return

        # --- Phase 2: Execution ---
        logging.info(f"Removing {len(manifest_entries_to_delete)} item(s) from '{active_config_name}'.")

        try:
            # Iterate in reverse to remove files before their parent directories
            for rel_path in reversed(sorted(list(manifest_entries_to_delete))):
                link_path = self.base_dir / rel_path
                copied_file_path = active_config_path / rel_path

                # Step 1: Unlink from base directory
                if link_path.is_symlink():
                    link_path.unlink()
                else:
                    logging.warning(f"Manifest path '{link_path}' was not a symlink. Skipping unlink.")

                # Step 2: Delete the copied file from the config directory
                if copied_file_path.is_file():
                    copied_file_path.unlink()
                    # Step 3 (Bonus): Clean up empty parent directories
                    try:
                        parent = copied_file_path.parent
                        while parent != active_config_path and not any(parent.iterdir()):
                            parent.rmdir()
                            parent = parent.parent
                    except OSError as e:
                        logging.warning(f"Could not clean up empty directory: {e}")
                else:
                    logging.warning(f"Copied file '{copied_file_path}' not found or is not a file. Skipping deletion.")

            # --- Phase 3: Commit ---
            new_manifest_paths = [p for p in current_manifest if p not in manifest_entries_to_delete]
            self._write_manifest(new_manifest_paths)

            print(f"Successfully removed specified paths from '{active_config_name}'.")
            logging.info("Removal complete and manifest updated.")
        except Exception as e:
            logging.critical(
                f"A critical error occurred during removal: {e}. The manifest may be out of sync.", exc_info=True)
            raise ConfigError(
                "Removal failed and the manifest has NOT been updated. Manual cleanup may be required.") from e
