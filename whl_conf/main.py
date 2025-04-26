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

import argparse
import logging
import sys


from aconf.config_manage import (
    ConfigManager,
    ConfigError,
    ConfigNotFoundError,
    ConfigAlreadyExistsError,
    ConfigRenameError)


# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
# You might want to increase level for debug during development
# logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(name)s:%(lineno)d - %(message)s')


def main():
    """Main function for the configuration management CLI."""
    parser = argparse.ArgumentParser(
        description="A configuration management tool.",
        formatter_class=argparse.RawTextHelpFormatter  # Keep formatting in help messages
    )

    # Optional: Add a global argument for the config base directory
    parser.add_argument(
        "--conf-dir",
        type=str,
        default='.',  # Default to current directory for 'Confs'
        help="Base directory for configuration management (where the 'Confs' folder is located)."
    )

    # Use subparsers for different commands
    subparsers = parser.add_subparsers(
        dest='command',  # Store the command name in args.command
        required=True,  # Make the command argument required
        help='Available commands'
    )

    # --- 'list' command ---
    parser_list = subparsers.add_parser(
        'list',
        help='List all available configurations and mark the active one.'
    )
    # list command needs no arguments

    # --- 'info' command ---
    parser_info = subparsers.add_parser(
        'info',
        help='Show information (from package.yaml) for a specific configuration.'
    )
    parser_info.add_argument(
        'config_name',
        type=str,
        help='The name of the configuration to show info for.'
    )

    # --- 'delete' command ---
    parser_delete = subparsers.add_parser(
        'delete',
        aliases=['del'],  # Allow 'del' as an alias
        help='Delete a configuration.'
    )
    parser_delete.add_argument(
        'config_name',
        type=str,
        help='The name of the configuration to delete.'
    )

    # --- 'create' command ---
    parser_create = subparsers.add_parser(
        'create',
        help='Create a new configuration from a template.'
    )
    parser_create.add_argument(
        'template_name',
        type=str,
        help='The name of the template configuration to copy from (located in Confs/Templates/).'
    )
    parser_create.add_argument(
        'new_config_name',
        type=str,
        help='The name for the new configuration.'
    )

    # --- 'activate' command ---
    parser_activate = subparsers.add_parser(
        'activate',
        help='Set the currently active configuration.'
    )
    parser_activate.add_argument(
        'config_name',
        type=str,
        help='The name of the configuration to activate.'
    )

    # --- 'update' command (corresponds to save_config) ---
    # Renamed to 'update' to be more descriptive of copying from a source
    parser_update = subparsers.add_parser(
        'update',
        # Aliases like ['save'] could be added if desired, but 'update' is clearer
        help='Update a configuration by copying contents from a source directory.\n'
             'Corresponds to the original "save" functionality from source.'
    )
    parser_update.add_argument(
        'config_name',
        type=str,
        help='The name of the configuration to update (in Confs/).'
    )
    parser_update.add_argument(
        'source_dir',
        type=str,
        help='The path to the source directory containing files to copy.'
    )

    # --- 'diff' command ---
    parser_diff = subparsers.add_parser(
        'diff',
        help='Compare the contents of two configurations.'
    )
    parser_diff.add_argument(
        'config1_name',
        type=str,
        help='The name of the first configuration.'
    )
    parser_diff.add_argument(
        'config2_name',
        type=str,
        help='The name of the second configuration.'
    )

    # --- 'rename' command ---
    parser_rename = subparsers.add_parser(
        'rename',
        help='Rename a configuration.'
    )
    parser_rename.add_argument(
        'old_name',
        type=str,
        help='The current name of the configuration.'
    )
    parser_rename.add_argument(
        'new_name',
        type=str,
        help='The new name for the configuration.'
    )

    # Parse the arguments from command line (sys.argv[1:])
    args = parser.parse_args()

    # Instantiate the ConfigManager with the specified base directory
    try:
        conf_manager = ConfigManager(base_dir=args.conf_dir)
    except Exception as e:
        logging.error(f"Failed to initialize configuration manager: {e}")
        sys.exit(1)

    # Dispatch commands based on the parsed arguments
    try:
        if args.command == "list":
            configs = conf_manager.list_configs()
            print("Available Configurations:")
            for cfg in configs:
                print(f"- {cfg}")
            if not configs and conf_manager.get_active_config():
                # Special case: Active config might have been deleted externally
                print(
                    f"Active config '{conf_manager.get_active_config()}' not found in the list.")

        elif args.command == "info":
            info = conf_manager.get_config_info(args.config_name)
            if info is None:
                # get_config_info prints its own "not found" message
                pass  # info is None if package.yaml doesn't exist

        elif args.command == "delete":
            conf_manager.delete_config(args.config_name)

        elif args.command == "create":
            conf_manager.create_config(
                args.template_name, args.new_config_name)

        elif args.command == "activate":
            conf_manager.activate_config(args.config_name)

        elif args.command == "update":
            # Use the correct method name and arguments from our ConfigManager class
            conf_manager.update_config_from_directory(
                args.config_name, args.source_dir)

        elif args.command == "diff":
            # Use the correct method name and arguments
            conf_manager.diff_configs(args.config1_name, args.config2_name)

        elif args.command == "rename":
            # Use the correct method name and arguments
            conf_manager.rename_config(args.old_name, args.new_name)

        # Add more elif blocks for any other commands...

    except (ConfigNotFoundError, ConfigAlreadyExistsError, ConfigRenameError, ConfigError, FileNotFoundError, ValueError) as e:
        logging.error(f"Operation failed: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        # logging.debug("Traceback:", exc_info=True) # Uncomment for debugging traceback
        sys.exit(1)


if __name__ == "__main__":
    # Example of how to run main, useful for testing or if you don't
    # want it directly executed by `python main.py` but perhaps via a wrapper.
    # Normally, you would just call main() here.
    # main(sys.argv) # Pass sys.argv if you need to simulate specific args for testing
    main()  # Execute with actual command-line arguments
