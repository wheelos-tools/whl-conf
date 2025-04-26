#!/usr/bin/env python

# Copyright 2024 daohu527 <daohu527@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import os
import shutil
import yaml
import filecmp
from pathlib import Path
from typing import Optional, List, Dict, Any

# Custom exceptions for clear error handling


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
    Manages configurations stored in a dedicated directory structure,
    using a symbolic link at 'Confs/current' to indicate the active configuration.

    Supports structured checkout/save operations between the centralized
    'Confs' store and dispersed working directories using a 'layout.yaml' file.
    """

    def __init__(self, base_dir: str = '.'):
        """
        Initializes the ConfigurationManager.

        Args:
            base_dir: The base directory where the 'Confs' directory will be located.
                      Defaults to the current directory.
        Raises:
            ConfigPermissionError: If necessary directories cannot be created.
        """
        self.base_dir = Path(base_dir).resolve()
        self.confs_dir = self.base_dir / 'Confs'
        self.templates_dir = self.confs_dir / 'Templates'
        # The path for the symbolic link pointing to the active config
        self.current_link_path = self.confs_dir / 'current'

        self._ensure_directories_exist()

    def _ensure_directories_exist(self):
        """Ensures the base configuration and templates directories exist."""
        try:
            self.confs_dir.mkdir(parents=True, exist_ok=True)
            self.templates_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigPermissionError(
                f"Failed to create necessary directories in {self.base_dir}: {e}") from e

    def _get_config_path(self, config_name: str) -> Path:
        """
        Returns the absolute Path object for a given configuration name within confs_dir.

        Performs basic validation on the config_name.

        Args:
            config_name: The name of the configuration.

        Returns:
            The absolute Path object for the configuration directory.

        Raises:
            ValueError: If the config_name is invalid or a reserved name.
        """
        if not config_name:
            raise ValueError("Configuration name cannot be empty.")
        # Basic check for invalid characters that could cause path traversal or naming issues
        # Added ':' as it's used in Windows paths and could be problematic if not handled
        # Added null char
        if any(c in config_name for c in ['/', '\\', '..', ':', '\x00']):
            raise ValueError(f"Invalid configuration name: '{config_name}'")

        # Prevent treating 'current' or 'Templates' as regular config names via this method
        # Case-insensitive check for robustness
        if config_name.lower() in ['current', 'templates']:
            raise ValueError(f"'{config_name}' is a reserved name.")

        # Construct the path, resolve ensures it's absolute and cleans up '..' etc.
        # However, resolving here might hide issues if confs_dir itself doesn't exist,
        # better to just join and let is_dir() or other operations fail later if needed.
        # Let's return the joined path relative to confs_dir.
        config_path = self.confs_dir / config_name
        return config_path

    def _config_exists(self, config_name: str) -> bool:
        """Checks if a configuration directory exists and is not a special directory."""
        try:
            config_path = self._get_config_path(config_name)
            # Use is_dir() and explicit checks for special directories
            return config_path.is_dir() and not self._is_template_dir(config_path) and config_path.name.lower() != 'current'
        except ValueError:  # Handle invalid names caught by _get_config_path
            return False

    def _is_template_dir(self, config_path: Path) -> bool:
        """Checks if a given path is the templates directory."""
        # Use resolve() for robust comparison against resolved templates_dir
        # Handle potential FileNotFoundError if path or templates_dir doesn't exist yet
        try:
            return config_path.resolve() == self.templates_dir.resolve()
        except FileNotFoundError:
            return False  # If path or templates_dir doesn't exist, it's not the templates dir

    def _get_active_config_name_from_link(self) -> Optional[str]:
        """
        Reads the 'current' symlink and returns the active config name it points to.

        Returns:
            The name of the active configuration, or None if no valid link exists.
        """
        # Check if the link exists AND is specifically a symlink
        if not self.current_link_path.is_symlink():
            return None  # No active link or it's a file/directory named 'current'

        try:
            # Resolve the target path of the symlink to get the absolute path it points to
            target_path = self.current_link_path.resolve()

            # Check if the resolved target path is a directory and is within the confs_dir
            # using Path.is_relative_to (Python 3.9+) or checking parents (older Python)
            # is_relative_to is more explicit and robust
            if target_path.is_dir() and target_path.is_relative_to(self.confs_dir):
                # Extract the name relative to confs_dir. The name is the first part
                # of the relative path from confs_dir to the target directory.
                relative_target = target_path.relative_to(self.confs_dir)
                # config name is the top-level dir within confs
                return relative_target.parts[0]

            else:
                # Link is broken, points outside confs_dir, or points to a non-directory
                # It's good practice to clean up broken links if found, but resolving
                # is usually enough to determine validity without removal here.
                print(
                    f"Warning: Active link '{self.current_link_path}' points to an invalid location ({target_path}).")
                return None

        except FileNotFoundError:
            # Link exists but points to nowhere (broken link before resolve)
            print(
                f"Warning: Active link '{self.current_link_path}' points to a non-existent location.")
            return None
        except OSError as e:
            # Catch potential errors during resolve (e.g., permissions)
            print(
                f"Error resolving active symlink '{self.current_link_path}': {e}")
            return None
        except Exception as e:
            # Catch any other unexpected errors
            print(
                f"An unexpected error occurred while reading active symlink '{self.current_link_path}': {e}")
            return None

    def create_config(self, template_name: str, new_config_name: str):
        """
        Creates a new configuration from a template in the Templates directory.

        Args:
            template_name: The name of the template configuration to copy from
                           (expected to be a directory name within Confs/Templates/).
            new_config_name: The desired name for the new configuration.

        Raises:
            ValueError: If new_config_name is invalid.
            ConfigNotFoundError: If the template configuration does not exist.
            ConfigAlreadyExistsError: If a configuration with the new name already exists.
            ConfigError: If creation fails due to other reasons.
        """
        # _get_config_path validates new_config_name
        new_config_path = self._get_config_path(new_config_name)

        # Template path is relative to templates_dir
        template_path = self.templates_dir / template_name

        if not template_path.is_dir():
            raise ConfigNotFoundError(
                f"Template configuration '{template_name}' not found in {self.templates_dir}")

        if new_config_path.exists():
            raise ConfigAlreadyExistsError(
                f"Configuration '{new_config_name}' already exists at {new_config_path}")

        try:
            shutil.copytree(template_path, new_config_path)
            print(
                f"Configuration '{new_config_name}' created successfully from template '{template_name}'.")
        except Exception as e:
            # Clean up partially created directory if copy fails
            if new_config_path.exists():
                shutil.rmtree(new_config_path)
            raise ConfigError(
                f"Failed to create configuration '{new_config_name}': {e}") from e

    def delete_config(self, config_name: str):
        """
        Deletes a configuration directory from confs_dir.

        Args:
            config_name: The name of the configuration to delete.

        Raises:
            ValueError: If config_name is invalid.
            ConfigNotFoundError: If the configuration does not exist.
            ConfigError: If trying to delete the templates directory or a reserved name.
            ConfigPermissionError: If deletion fails due to permissions.
        """
        # _get_config_path validates config_name and checks reserved names
        config_path = self._get_config_path(config_name)

        if not config_path.is_dir():
            raise ConfigNotFoundError(
                f"Configuration '{config_name}' not found at {config_path}")

        if self._is_template_dir(config_path):
            raise ConfigError(
                f"Cannot delete the templates directory: '{config_name}'")

        # Check if it's the target of the active link
        active_name = self._get_active_config_name_from_link()
        if config_name == active_name:
            # Remove the symlink first if the target config is being deleted
            try:
                # Use missing_ok for robustness - the link might already be broken
                self.current_link_path.unlink(missing_ok=True)
                print(
                    f"Removed active link '{self.current_link_path}' as its target '{config_name}' is being deleted.")
            except OSError as e:
                raise ConfigPermissionError(
                    f"Failed to remove active symlink '{self.current_link_path}': {e}") from e
            except Exception as e:
                print(
                    f"Warning: An unexpected error occurred while removing active symlink: {e}")

        try:
            shutil.rmtree(config_path)
            print(f"Configuration '{config_name}' deleted successfully.")
        except OSError as e:
            raise ConfigPermissionError(
                f"Failed to delete configuration directory '{config_name}' at '{config_path}': {e}") from e
        except Exception as e:
            raise ConfigError(
                f"An unexpected error occurred while deleting configuration '{config_name}': {e}") from e

    def list_configs(self) -> List[str]:
        """
        Lists all available configurations (directories in confs_dir excluding special ones)
        and marks the active one based on the 'current' symlink.

        Returns:
            A list of configuration names (strings), with the active one marked by a '*'.
        """
        configs = []
        if not self.confs_dir.is_dir():
            # This should ideally not happen if _ensure_directories_exist ran, but defensive
            print(
                f"Warning: Configuration directory '{self.confs_dir}' does not exist.")
            return configs

        active_name = self._get_active_config_name_from_link()

        try:
            for entry in self.confs_dir.iterdir():
                # List only directories that are not the Templates dir and not the 'current' link/file
                # Use entry.name.lower() for case-insensitive check against reserved names
                if entry.is_dir() and not self._is_template_dir(entry) and entry.name.lower() != 'current':
                    config_name = entry.name
                    if config_name == active_name:
                        configs.append(f"* {config_name}")
                    else:
                        configs.append(config_name)
                # Optional: Warn about unexpected entries named 'current' that are not symlinks
                elif entry.name.lower() == 'current' and not entry.is_symlink():
                    print(
                        f"Warning: Found non-symlink entry named '{entry.name}' in '{self.confs_dir}'. Ignoring it in config list.")
        except OSError as e:
            print(f"Error listing configurations in '{self.confs_dir}': {e}")
            # Return partial list or raise error? Returning partial list might be more user-friendly
            pass

        # Only say "No configs" if Templates also doesn't exist
        if not configs and not self.templates_dir.is_dir():
            print("No configurations found.")
        elif not configs and self.templates_dir.is_dir():
            print("No user configurations found (Templates directory exists).")

        return configs

    def activate_config(self, config_name: str):
        """
        Sets the specified configuration as the currently active one by creating a symlink
        at 'Confs/current' pointing to the configuration's directory.

        Args:
            config_name: The name of the configuration to activate.

        Raises:
            ValueError: If config_name is invalid.
            ConfigNotFoundError: If the configuration does not exist.
            ConfigError: If trying to activate the templates directory or a reserved name.
            ConfigPermissionError: If symlink creation/removal fails.
        """
        # _get_config_path validates config_name and checks reserved names
        config_path = self._get_config_path(config_name)

        if not config_path.is_dir():
            raise ConfigNotFoundError(
                f"Configuration '{config_name}' not found at {config_path}")

        if self._is_template_dir(config_path):
            raise ConfigError(
                f"Cannot activate the templates directory: '{config_name}'")

        try:
            # Remove existing symlink if it exists. Use missing_ok=True for robustness.
            self.current_link_path.unlink(missing_ok=True)
        except OSError as e:
            raise ConfigPermissionError(
                f"Failed to remove existing active symlink '{self.current_link_path}': {e}") from e
        except Exception as e:
            print(
                f"Warning: An unexpected error occurred while removing existing active symlink: {e}")

        try:
            # Create the new symlink pointing to the target config's absolute path.
            # Using absolute path for the target is more robust.
            self.current_link_path.symlink_to(config_path.resolve())
            print(
                f"Configuration '{config_name}' is now active (symlink created at '{self.current_link_path}').")
        except OSError as e:
            raise ConfigPermissionError(
                f"Failed to create symlink from '{self.current_link_path}' to '{config_path.resolve()}': {e}") from e
        except Exception as e:
            raise ConfigError(
                f"An unexpected error occurred during symlink creation: {e}") from e

    def deactivate_config(self):
        """
        Deactivates the current configuration by removing the 'Confs/current' symlink.
        """
        if not self.current_link_path.is_symlink():
            print("No active configuration to deactivate.")
            return

        try:
            self.current_link_path.unlink()
            print(
                f"Active configuration deactivated (symlink '{self.current_link_path}' removed).")
        except OSError as e:
            raise ConfigPermissionError(
                f"Failed to remove active symlink '{self.current_link_path}': {e}") from e
        except Exception as e:
            print(f"An unexpected error occurred during deactivation: {e}") from e

    def get_active_config(self) -> Optional[str]:
        """
        Returns the name of the currently active configuration by reading the 'Confs/current'
        symlink, or None if no valid link exists.
        """
        return self._get_active_config_name_from_link()

    def diff_configs(self, config1_name: str, config2_name: str):
        """
        Compares the contents of two configuration directories in confs_dir
        and prints the differences.

        Args:
            config1_name: The name of the first configuration.
            config2_name: The name of the second configuration.

        Raises:
            ValueError: If config names are invalid.
            ConfigNotFoundError: If either configuration does not exist.
            ConfigError: For other unexpected errors during comparison.
        """
        # _get_config_path validates names and checks reserved names
        config1_path = self._get_config_path(config1_name)
        config2_path = self._get_config_path(config2_name)

        if not config1_path.is_dir():
            raise ConfigNotFoundError(
                f"Configuration '{config1_name}' not found at {config1_path}")
        if not config2_path.is_dir():
            raise ConfigNotFoundError(
                f"Configuration '{config2_name}' not found at {config2_path}")

        print(f"Comparing '{config1_name}' and '{config2_name}':")
        # Use filecmp.dircmp for directory comparison
        # Ignore the layout.yaml and package.yaml files themselves in the diff?
        # Or include them? Let's include them by default unless specified.
        # Ignore the 'current' symlink if it somehow exists inside a config dir
        ignore_patterns = ['current']  # List directory/file names to ignore

        comparison = filecmp.dircmp(
            config1_path, config2_path, ignore=ignore_patterns)

        def print_diff_report(dcmp):
            # Get relative paths for cleaner output
            left_rel = Path(dcmp.left).relative_to(self.confs_dir)
            right_rel = Path(dcmp.right).relative_to(self.confs_dir)

            # Files unique to the first directory
            if dcmp.left_only:
                print(f"  Only in {left_rel}: {dcmp.left_only}")
            # Files unique to the second directory
            if dcmp.right_only:
                print(f"  Only in {right_rel}: {dcmp.right_only}")
            # Files that are different
            if dcmp.diff_files:
                print(f"  Differing files: {dcmp.diff_files}")
            # Files that are identical (optional, usually noisy)
            # if dcmp.same_files:
            #     print(f"  Identical files: {dcmp.same_files}")

            # Recursively compare subdirectories
            for name, sub_dcmp in dcmp.subdirs.items():
                # filecmp dircmp automatically ignores names in the 'ignore' list
                # but we can add extra checks if needed.
                # Recursively call print_diff_report for subdirectories
                print_diff_report(sub_dcmp)

        try:
            print_diff_report(comparison)

            if not comparison.left_only and not comparison.right_only and not comparison.diff_files and not comparison.subdirs:
                print("  Configurations are identical.")

        except Exception as e:
            raise ConfigError(
                f"An unexpected error occurred during diff report generation: {e}") from e

    def rename_config(self, old_name: str, new_name: str):
        """
        Renames a configuration directory within confs_dir.

        Args:
            old_name: The current name of the configuration.
            new_name: The new name for the configuration.

        Raises:
            ValueError: If names are invalid.
            ConfigNotFoundError: If the old configuration does not exist.
            ConfigAlreadyExistsError: If a configuration with the new name already exists.
            ConfigError: If trying to rename the templates directory or a reserved name.
            ConfigRenameError: If renaming fails.
            ConfigPermissionError: If file operations fail due to permissions.
        """
        # _get_config_path validates names and checks reserved names
        old_path = self._get_config_path(old_name)
        new_path = self._get_config_path(new_name)

        if not old_path.is_dir():
            raise ConfigNotFoundError(
                f"Configuration '{old_name}' not found at {old_path}")

        if new_path.exists():  # Check if the new name already exists (as file or directory)
            raise ConfigAlreadyExistsError(
                f"Cannot rename to '{new_name}'. A file or directory with this name already exists at {new_path}")

        if self._is_template_dir(old_path):
            raise ConfigError(
                f"Cannot rename the templates directory: '{old_name}'")

        # Check if the configuration being renamed is currently active
        is_active = (self._get_active_config_name_from_link() == old_name)

        try:
            # Perform the directory rename
            old_path.rename(new_path)
            print(
                f"Configuration '{old_name}' renamed to '{new_name}' successfully.")

            # If the renamed config was active, update the symlink to point to the new location
            if is_active:
                try:
                    # Remove the old symlink (it points to the old path which is gone after rename)
                    # Use missing_ok=True just in case, although if is_active was true, it should exist.
                    self.current_link_path.unlink(missing_ok=True)
                    # Create a new symlink pointing to the new absolute path
                    self.current_link_path.symlink_to(new_path.resolve())
                    print(
                        f"Active link '{self.current_link_path}' updated to point to '{new_name}'.")
                except OSError as e:
                    # This is a critical failure - the link is broken after rename
                    raise ConfigPermissionError(
                        f"Failed to update active symlink during rename from '{old_name}' to '{new_name}'. Active link may be broken. Error: {e}") from e
                except Exception as e:
                    print(
                        f"Warning: An unexpected error occurred while updating active symlink during rename: {e}")

        except OSError as e:
            raise ConfigRenameError(
                f"Failed to rename configuration directory from '{old_name}' to '{new_name}': {e}") from e
        except Exception as e:
            raise ConfigRenameError(
                f"An unexpected error occurred during rename: {e}") from e

    def get_config_info(self, config_name: str) -> Optional[Dict[str, Any]]:
        """
        Reads and returns information from the package.yaml file of a configuration.

        Args:
            config_name: The name of the configuration.

        Returns:
            A dictionary containing the YAML data, or None if package.yaml is not found.

        Raises:
            ValueError: If config_name is invalid.
            ConfigNotFoundError: If the configuration does not exist.
            ConfigError: If package.yaml exists but cannot be parsed or read.
        """
        # _get_config_path validates config_name and checks reserved names
        config_path = self._get_config_path(config_name)
        package_yaml_path = config_path / 'package.yaml'

        if not config_path.is_dir():
            raise ConfigNotFoundError(
                f"Configuration '{config_name}' not found at {config_path}")

        if not package_yaml_path.is_file():
            print(
                f"Info: '{package_yaml_path}' not found for configuration '{config_name}'.")
            return None

        try:
            with open(package_yaml_path, 'r') as f:
                info = yaml.safe_load(f)
            print(f"Info for configuration '{config_name}':")
            # Use yaml.dump for pretty printing dictionary/list info
            # Handle None or non-dict/list types gracefully
            if isinstance(info, (dict, list)):
                print(yaml.dump(info, indent=2, default_flow_style=False))
            elif info is None:
                print("  (Empty or null content)")
            else:
                print(f"  {info}")  # Print raw value if not dict/list/None
            return info
        except FileNotFoundError:
            # This case is already handled by package_yaml_path.is_file() check above,
            # but defensive programming doesn't hurt.
            print(
                f"Info: '{package_yaml_path}' not found for configuration '{config_name}'.")
            return None
        except yaml.YAMLError as e:
            raise ConfigError(
                f"Failed to parse package.yaml for configuration '{config_name}': {e}")
        except Exception as e:
            raise ConfigError(
                f"An error occurred while reading package.yaml for configuration '{config_name}': {e}")

    def _get_layout_mappings(self, config_name: str) -> List[Dict[str, str]]:
        """
        Reads and validates the layout.yaml file for a configuration.

        Args:
            config_name: The name of the configuration.

        Returns:
            A list of file mapping dictionaries.

        Raises:
            ValueError: If config_name is invalid.
            ConfigNotFoundError: If the configuration directory does not exist.
            ConfigLayoutError: If layout.yaml is missing, unreadable, invalid,
                               or has incorrect format.
        """
        # _get_config_path validates config_name and checks reserved names
        config_path = self._get_config_path(config_name)
        layout_path = config_path / 'layout.yaml'

        if not config_path.is_dir():
            raise ConfigNotFoundError(
                f"Configuration '{config_name}' not found at {config_path}")

        if not layout_path.is_file():
            raise ConfigLayoutError(
                f"Layout file '{layout_path}' not found for configuration '{config_name}'. Cannot perform structured copy.")

        try:
            with open(layout_path, 'r') as f:
                layout_data = yaml.safe_load(f)
        except FileNotFoundError:  # Defensive, should be caught by is_file()
            raise ConfigLayoutError(
                f"Layout file '{layout_path}' not found (unexpected).")
        except yaml.YAMLError as e:
            raise ConfigLayoutError(
                f"Failed to parse layout.yaml for '{config_name}': {e}")
        except Exception as e:
            raise ConfigLayoutError(
                f"Failed to read layout.yaml for '{config_name}': {e}")

        # Validate structure
        if not isinstance(layout_data, dict) or 'file_mappings' not in layout_data or not isinstance(layout_data['file_mappings'], list):
            # Allow None or empty list for 'file_mappings'
            if layout_data is None or layout_data == {} or ('file_mappings' in layout_data and (layout_data['file_mappings'] is None or layout_data['file_mappings'] == [])):
                print(
                    f"Layout file '{layout_path}' is empty or has no mappings. No files will be copied.")
                return []  # Return empty list if no mappings

            raise ConfigLayoutError(
                f"Invalid layout.yaml format for '{config_name}'. Expected a dict with a 'file_mappings' list. Got: {type(layout_data)}")

        mappings = layout_data['file_mappings']
        if not isinstance(mappings, list):  # Re-check after handling None/empty dict
            raise ConfigLayoutError(
                f"Invalid layout.yaml format for '{config_name}'. 'file_mappings' must be a list. Got: {type(mappings)}")

        for i, mapping in enumerate(mappings):
            if not isinstance(mapping, dict):
                raise ConfigLayoutError(
                    f"Invalid mapping format in layout.yaml for '{config_name}' at index {i}. Expected a dictionary. Got: {type(mapping)}")
            if 'confs_path' not in mapping or 'working_path' not in mapping:
                raise ConfigLayoutError(
                    f"Invalid mapping format in layout.yaml for '{config_name}' at index {i}. Missing 'confs_path' or 'working_path'. Mapping: {mapping}")
            if not isinstance(mapping['confs_path'], str) or not isinstance(mapping['working_path'], str):
                raise ConfigLayoutError(
                    f"Invalid path types in layout.yaml mapping for '{config_name}' at index {i}. Paths must be strings. Mapping: {mapping}")
            # TODO: Add stricter path validation here (e.g., disallow '..' in paths)

        print(
            f"Successfully loaded layout for '{config_name}' from '{layout_path}'. Found {len(mappings)} mappings.")
        return mappings

    def checkout_config(self, config_name: str, target_working_dir: str):
        """
        Copies configuration files from confs/config_name to a target working directory
        based on the layout.yaml file.

        Args:
            config_name: The name of the configuration to checkout.
            target_working_dir: The path to the directory where files will be copied.

        Raises:
            ValueError: If config_name is invalid.
            ConfigNotFoundError: If the configuration in confs does not exist.
            FileNotFoundError: If the target working directory does not exist,
                                or if a source file specified in layout.yaml is missing in confs.
            ConfigLayoutError: If layout.yaml is missing, unreadable, or invalid.
            ConfigPermissionError: If file operations fail due to permissions.
            ConfigError: For other unexpected errors during copy.
        """
        # _get_config_path validates config_name
        config_path = self._get_config_path(config_name)
        # Resolve target path early
        target_path = Path(target_working_dir).resolve()

        if not config_path.is_dir():
            raise ConfigNotFoundError(
                f"Configuration '{config_name}' not found at {config_path}")

        if not target_path.is_dir():
            # Decide whether to create the target directory or require it to exist
            # Requiring it might be safer to avoid accidental creation
            raise FileNotFoundError(
                f"Target working directory '{target_path}' not found.")
            # Alternative: try: target_path.mkdir(parents=True, exist_ok=True) except OSError as e: ...

        mappings = self._get_layout_mappings(
            config_name)  # This handles layout errors

        # If mappings list is empty, nothing to copy, just report
        if not mappings:
            print(
                f"No file mappings defined in layout for '{config_name}'. Checkout skipped.")
            return

        print(
            f"Checking out configuration '{config_name}' ({len(mappings)} files) to '{target_path}'.")

        for mapping in mappings:
            # Source is in confs relative to config_path
            confs_file = config_path / mapping['confs_path']
            # Target is in working dir relative to target_path
            working_file = target_path / mapping['working_path']

            if not confs_file.is_file():
                # If a file defined in layout is missing from the confs source,
                # it indicates the config definition is incomplete or damaged. Stop.
                raise FileNotFoundError(
                    f"Source file '{mapping['confs_path']}' specified in layout.yaml not found in '{config_path}'.")

            try:
                # Ensure the target directory structure exists in the working dir
                working_file.parent.mkdir(parents=True, exist_ok=True)
                # Copy the file, preserving metadata (like modification time)
                shutil.copy2(confs_file, working_file)
                # print(f"  Copied '{mapping['confs_path']}' to '{mapping['working_path']}'") # Optional: Verbose output
            except OSError as e:
                raise ConfigPermissionError(
                    f"Failed to copy file during checkout from '{confs_file}' to '{working_file}': {e}") from e
            except Exception as e:
                # Catch any other unexpected errors during file copy
                raise ConfigError(
                    f"An unexpected error occurred while copying '{confs_file}' during checkout: {e}") from e

        print(
            f"Checkout of configuration '{config_name}' completed successfully.")

    def save_config(self, config_name: str, source_working_dir: str):
        """
        Saves configuration files from a source working directory to confs/config_name
        based on the layout.yaml file. This is the reverse of checkout.

        Args:
            config_name: The name of the configuration in confs to save to/update.
            source_working_dir: The path to the source working directory containing modified files.

        Raises:
            ValueError: If config_name is invalid.
            ConfigNotFoundError: If the target configuration in confs does not exist.
            FileNotFoundError: If the source working directory does not exist,
                                or if a source file specified in layout.yaml is missing in the working directory.
            ConfigLayoutError: If layout.yaml is missing, unreadable, or invalid.
            ConfigPermissionError: If file operations fail due to permissions.
            ConfigError: For other unexpected errors during copy.
        """
        # _get_config_path validates config_name
        config_path = self._get_config_path(config_name)  # Target in confs
        # Resolve source path early
        source_path = Path(source_working_dir).resolve()

        if not config_path.is_dir():
            raise ConfigNotFoundError(
                f"Target configuration '{config_name}' not found at {config_path}")

        if not source_path.is_dir():
            raise FileNotFoundError(
                f"Source working directory '{source_path}' not found.")

        mappings = self._get_layout_mappings(
            config_name)  # This handles layout errors

        # If mappings list is empty, nothing to copy, just report
        if not mappings:
            print(
                f"No file mappings defined in layout for '{config_name}'. Save skipped.")
            return

        print(
            f"Saving configuration '{config_name}' ({len(mappings)} files) from '{source_path}'.")

        for mapping in mappings:
            # Source is in working dir relative to source_path
            working_file = source_path / mapping['working_path']
            # Target is in confs relative to config_path
            confs_file = config_path / mapping['confs_path']

            if not working_file.is_file():
                # If a file defined in layout is missing from the working source,
                # it indicates the working dir state is incomplete or doesn't match the expected structure.
                # This is an error as we can't save something that isn't there. Stop.
                raise FileNotFoundError(
                    f"Source file '{mapping['working_path']}' specified in layout.yaml not found in working directory '{source_path}'.")

            try:
                # Ensure the target directory structure exists in confs
                confs_file.parent.mkdir(parents=True, exist_ok=True)
                # Copy the file, preserving metadata
                shutil.copy2(working_file, confs_file)
                # print(f"  Saved '{mapping['working_path']}' to '{mapping['confs_path']}'") # Optional: Verbose output
            except OSError as e:
                raise ConfigPermissionError(
                    f"Failed to copy file during save from '{working_file}' to '{confs_file}': {e}") from e
            except Exception as e:
                # Catch any other unexpected errors during file copy
                raise ConfigError(
                    f"An unexpected error occurred while copying '{working_file}' during save: {e}") from e

        print(
            f"Save of configuration to '{config_name}' completed successfully.")


# --- Example Usage ---
if __name__ == "__main__":
    # Clean up previous test runs
    test_base_dir = Path('./test_configs_symlink')
    if test_base_dir.exists():
        print(f"Cleaning up existing test directory: {test_base_dir}")
        shutil.rmtree(test_base_dir)

    print(
        f"\n--- Initializing Configuration Manager in {test_base_dir} (using symlinks) ---")
    try:
        manager = ConfigManager(base_dir=test_base_dir)
    except ConfigPermissionError as e:
        print(
            f"Initialization failed: {e}. Please check directory permissions.")
        exit(1)

    print("\n--- Listing configs (should be empty except Templates) ---")
    print(manager.list_configs())

    # Create a dummy template
    print("\n--- Creating a dummy template 'basic_template' ---")
    dummy_template_path = manager.templates_dir / 'basic_template'
    dummy_template_path.mkdir(exist_ok=True)
    (dummy_template_path /
     'config.ini').write_text("[DEFAULT]\nuser=guest\nport=8080\n")
    (dummy_template_path / 'package.yaml').write_text(
        "name: BasicTemplate\nversion: 1.0\ndescription: A basic template config.\n")
    (dummy_template_path / 'data').mkdir(exist_ok=True)
    (dummy_template_path / 'data' / 'file1.txt').write_text("template data")

    print("\n--- Creating Test1 from template ---")
    try:
        manager.create_config('basic_template', 'Test1')
    except ConfigError as e:
        print(e)

    print("\n--- Creating Test2 from template ---")
    try:
        manager.create_config('basic_template', 'Test2')
    except ConfigError as e:
        print(e)

    print("\n--- Listing configs ---")
    print(manager.list_configs())

    print("\n--- Activating Test1 ---")
    try:
        manager.activate_config('Test1')
    except ConfigError as e:
        print(e)

    print("\n--- Listing configs (with active) ---")
    print(manager.list_configs())
    print(f"Current active config: {manager.get_active_config()}")
    print(
        f"Check symlink target: {manager.current_link_path} -> {manager.current_link_path.resolve() if manager.current_link_path.is_symlink() else 'Not a symlink'}")

    print("\n--- Activating Test2 ---")
    try:
        manager.activate_config('Test2')
    except ConfigError as e:
        print(e)

    print("\n--- Listing configs (Test2 active) ---")
    print(manager.list_configs())
    print(f"Current active config: {manager.get_active_config()}")
    print(
        f"Check symlink target: {manager.current_link_path} -> {manager.current_link_path.resolve() if manager.current_link_path.is_symlink() else 'Not a symlink'}")

    print("\n--- Getting Info for Test2 ---")
    try:
        manager.get_config_info('Test2')
    except ConfigError as e:
        print(e)

    # Create a dummy source directory for update_config_from_directory
    source_for_update = test_base_dir / 'source_updates'
    source_for_update.mkdir(exist_ok=True)
    (source_for_update / 'new_config.json').write_text('{"data": "updated"}')
    # Update package.yaml too
    (source_for_update / 'package.yaml').write_text('name: Test2Updated\nversion: 1.1\n')
    # Update existing file
    (source_for_update / 'data' / 'file1.txt').write_text("updated data content")
    (source_for_update / 'data' / 'new_file.log').write_text("some logs")  # Add new file

    print("\n--- Updating Test2 from source directory (while active) ---")
    try:
        manager.update_config_from_directory('Test2', str(source_for_update))
    except ConfigError as e:
        print(e)

    print("\n--- Getting Info for Test2 after update ---")
    try:
        manager.get_config_info('Test2')
    except ConfigError as e:
        print(e)

    print("\n--- Diffing Test1 and Test2 after update ---")
    try:
        # Should show differences after update
        manager.diff_configs('Test1', 'Test2')
    except ConfigError as e:
        print(e)

    print("\n--- Renaming Test1 to OldTest1 ---")
    try:
        manager.rename_config('Test1', 'OldTest1')
    except ConfigError as e:
        print(e)

    print("\n--- Listing configs after rename ---")
    print(manager.list_configs())

    print("\n--- Renaming active config (Test2) to NewTest2 ---")
    try:
        manager.rename_config('Test2', 'NewTest2')
        # Should show NewTest2
        print(
            f"Active config after renaming active one: {manager.get_active_config()}")
        print(
            f"Check symlink target after renaming active: {manager.current_link_path} -> {manager.current_link_path.resolve() if manager.current_link_path.is_symlink() else 'Not a symlink'}")

    except ConfigError as e:
        print(e)

    print("\n--- Listing configs after renaming active ---")
    print(manager.list_configs())

    print("\n--- Deleting OldTest1 ---")
    try:
        manager.delete_config('OldTest1')
    except ConfigError as e:
        print(e)

    print("\n--- Listing configs after deletion ---")
    print(manager.list_configs())

    print("\n--- Attempting to delete active config (NewTest2) ---")
    try:
        # This should now succeed, but will remove the symlink first
        manager.delete_config('NewTest2')
        # Should be None
        print(
            f"Active config after deleting formerly active one: {manager.get_active_config()}")
        # Should be False
        print(f"Check symlink status: {manager.current_link_path.exists()}")
    except ConfigError as e:
        print(e)
    except ConfigPermissionError as e:
        print(f"Deletion failed due to permissions: {e}")

    print("\n--- Listing configs after deleting NewTest2 ---")
    print(manager.list_configs())  # Only Templates should be left

    print("\n--- Cleaning up test directory ---")
    # shutil.rmtree(test_base_dir) # Uncomment to auto-cleanup
