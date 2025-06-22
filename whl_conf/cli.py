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
from typing import Dict, Callable, Any

from whl_conf.config import ConfigManager
from whl_conf.config import (
    ConfigError,
    ConfigNotFoundError,
    ConfigAlreadyExistsError,
    ConfigPermissionError
)

# ==============================================================================
# Command Handlers
# ==============================================================================


def handle_list(manager: ConfigManager, args: argparse.Namespace):
    """Handle 'list' command"""
    manager.list_configs()


def handle_info(manager: ConfigManager, args: argparse.Namespace):
    """Handle 'info' command"""
    manager.show_info(args.config_name)


def handle_create(manager: ConfigManager, args: argparse.Namespace):
    """Handle 'create' command"""
    manager.create_config(args.template_name, args.new_config_name)
    logging.info(
        f"Config '{args.new_config_name}' successfully created from template '{args.template_name}'.")


def handle_delete(manager: ConfigManager, args: argparse.Namespace):
    """Handle 'delete' command"""
    manager.delete_config(args.config_name, force=args.force)


def handle_activate(manager: ConfigManager, args: argparse.Namespace):
    """Handle 'activate' command"""
    manager.activate_config(args.config_name, dry_run=args.dry_run)


def handle_diff(manager: ConfigManager, args: argparse.Namespace):
    """Handle 'diff' command"""
    manager.diff_configs(args.config1_name, args.config2_name)


def handle_rename(manager: ConfigManager, args: argparse.Namespace):
    """Handle 'rename' command"""
    manager.rename_config(args.old_name, args.new_name)


def handle_export(manager: ConfigManager, args: argparse.Namespace):
    """Handle 'export' command"""
    manager.export_config(args.archive_path)


def handle_import(manager: ConfigManager, args: argparse.Namespace):
    """Handle 'import' command"""
    manager.import_config(args.archive_file, auto_active=args.activate)

# ==============================================================================
# Main Application
# ==============================================================================


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the command-line argument parser"""
    parser = argparse.ArgumentParser(
        description="whl-conf: Centralized autonomous driving config set management tool",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # Global arguments
    parser.add_argument(
        "--conf-dir", type=str, default=".",
        help="Config repository root directory (contains confs/ folder), default is current directory"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose debug output"
    )

    subparsers = parser.add_subparsers(
        dest='command', required=True, help='Available commands'
    )

    # 1. list
    subparsers.add_parser("list", help="List all available configs")
    # 2. info
    parser_info = subparsers.add_parser(
        "info", help="Show details of the specified config")
    parser_info.add_argument("config_name", help="Config name")
    # 3. create
    parser_create = subparsers.add_parser(
        "create", help="Create a new config set from a template")
    parser_create.add_argument("template_name", help="Template config name")
    parser_create.add_argument("new_config_name", help="New config name")
    # 4. delete
    parser_delete = subparsers.add_parser(
        "delete", help="Delete the specified config")
    parser_delete.add_argument("config_name", help="Config name")
    parser_delete.add_argument(
        "--force", action="store_true", help="Skip confirmation, force delete")
    # 5. activate
    parser_activate = subparsers.add_parser(
        "activate", help="Activate the specified config set")
    parser_activate.add_argument("config_name", help="Config name")
    parser_activate.add_argument(
        "--dry-run", action="store_true", help="Print actions only, do not execute")
    # 6. diff
    parser_diff = subparsers.add_parser(
        "diff", help="Compare file contents of two config sets")
    parser_diff.add_argument("config1_name", help="First config name")
    parser_diff.add_argument("config2_name", help="Second config name")
    # 7. rename
    parser_rename = subparsers.add_parser("rename", help="Rename a config set")
    parser_rename.add_argument("old_name", help="Old config name")
    parser_rename.add_argument("new_name", help="New config name")
    # 8. export
    parser_export = subparsers.add_parser(
        "export", help="Export the currently active config as a tar.gz archive")
    parser_export.add_argument("archive_path", help="Archive output path")
    # 9. import
    parser_import = subparsers.add_parser(
        "import", help="Import a tar.gz config archive")
    parser_import.add_argument("archive_file", help="tar.gz archive path")
    parser_import.add_argument(
        "--activate", action="store_true", help="Activate immediately after import")

    return parser


def main():
    """Main execution function"""
    parser = create_parser()
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level, format='%(levelname)s: %(message)s', stream=sys.stdout)

    # Output logs to stdout instead of stderr, which is more typical for CLI tools

    try:
        # Initialize core manager
        conf_manager = ConfigManager(base_dir=args.conf_dir)

        # Command handler mapping
        command_handlers: Dict[str, Callable[[ConfigManager, argparse.Namespace], None]] = {
            "list": handle_list,
            "info": handle_info,
            "create": handle_create,
            "delete": handle_delete,
            "activate": handle_activate,
            "diff": handle_diff,
            "rename": handle_rename,
            "export": handle_export,
            "import": handle_import,
        }

        # Get and execute the command handler
        handler = command_handlers.get(args.command)
        if handler:
            handler(conf_manager, args)
        else:
            # This should not happen since 'command' is required
            parser.print_help()
            sys.exit(1)

    # Precise, user-facing exception handling
    except ConfigNotFoundError as e:
        logging.error(
            f"Operation failed: Specified config not found. Details: {e}")
        sys.exit(2)  # Use different exit codes
    except ConfigAlreadyExistsError as e:
        logging.error(f"Operation failed: Config already exists. Details: {e}")
        sys.exit(3)
    except ConfigPermissionError as e:
        logging.error(
            f"Operation failed: Permission denied. Please check file/directory permissions. Details: {e}")
        sys.exit(4)
    except ConfigError as e:
        # Catch all other business logic errors
        logging.error(f"Error occurred: {e}")
        sys.exit(1)
    except FileNotFoundError as e:
        # Catch file system errors, e.g., file not found during import/export
        logging.error(
            f"File system error: File or directory not found. Details: {e}")
        sys.exit(5)
    except Exception as e:
        # Catch all unexpected exceptions
        logging.error("An unexpected critical error occurred.")
        if args.verbose:
            # Print stack trace in verbose mode for debugging
            logging.exception(e)
        else:
            logging.error(f"Details: {e}")
        sys.exit(127)


if __name__ == "__main__":
    main()
