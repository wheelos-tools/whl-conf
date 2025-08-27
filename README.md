# whl-conf

**whl-conf** is a command-line tool for managing configuration sets (configs), particularly suited for autonomous driving applications. It provides a robust, manifest-driven workflow for activating, versioning, and modifying configurations safely.

## Global Options

*   `--conf-dir <path>`: Specifies the root directory of the configuration repository. All operations, such as creating configs and managing links, are performed relative to this directory. The default is `/apollo`.

## Command Descriptions

*   `list`: Displays all available configuration sets.

*   `info <config_name>`: Shows detailed information about the specified configuration set.

*   `create <new_config_name>`: Creates a new configuration set based on the specified template.

*   `delete <config_name>`: Deletes the specified configuration set.

*   `activate <config_name> [--dry-run]`: Activates the specified configuration set, creating symlinks for all managed files. Use `--dry-run` to preview the activation without making changes.

*   `add <path(s)> [--dry-run]`: Adds one or more files/directories to the **active** configuration. This copies the source files into the active config's directory (creating a snapshot) and then creates symlinks pointing to these new copies. This operation is transactional and forcefully overwrites any existing files at the destination. Use `--dry-run` to preview the actions.

*   `remove <path(s)> [--dry-run]`: Removes one or more files/directories from the **active** configuration. This is the mirror operation to `add`. It deletes both the system symlinks and the corresponding file snapshots from within the active config's directory. Use `--dry-run` to preview the actions.

*   `diff <config1_name> <config2_name>`: Compares the contents of two configuration sets and shows the differences.

*   `rename <old_name> <new_name>`: Renames the specified configuration set.

*   `pull [--name <name>]`: Pulls a configuration set from a remote source and saves it locally. Optionally, specify a local name using `--name`.

---

## Quick Start

**Install the tool**

```bash
pip install whl-conf
```

## Commands

1.  **List all configuration sets**

    ```bash
    whl-conf list
    ```

2.  **Show details of a specific config**

    ```bash
    whl-conf info <config_name>
    ```

3.  **Create a new config set from a template**

    ```bash
    whl-conf create <template_name> <new_config_name>
    ```

4.  **Delete a specified config**

    ```bash
    whl-conf delete <config_name>
    ```

5.  **Activate a specified config set**

    ```bash
    whl-conf activate <config_name>
    ```

6.  **Add a new model file to the active config**

    ```bash
    whl-conf add modules/perception/models/new_model.pb
    ```

7.  **Add multiple files and directories to the active config**

    ```bash
    whl-conf add modules/control/conf/new_params.pb.txt data/calibration/new_camera/
    ```

8.  **Remove a file from the active config**

    ```bash
    whl-conf remove modules/perception/models/new_model.pb
    ```

9.  **Compare two config sets**

    ```bash
    whl-conf diff <config1_name> <config2_name>
    ```

10. **Rename a config set**

    ```bash
    whl-conf rename <old_name> <new_name>
    ```

11. **Pull a config set from a remote source**

    ```bash
    whl-conf pull [--name <name>]
    ```

---

For more detailed information and additional options, you can run:

```bash
whl-conf --help
whl-conf <command> --help
```
