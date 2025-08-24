# whl-conf

**whl-conf** is a command-line tool for managing configuration sets (configs), particularly suited for autonomous driving applications.

## Command Descriptions

* `list`: Displays all available configuration sets.

* `info <config_name>`: Shows detailed information about the specified configuration set.

* `create <new_config_name>`: Creates a new configuration set based on the specified template.

* `delete <config_name>`: Deletes the specified configuration set.

* `activate <config_name> [--dry_run]`: Activates the specified configuration set. Use `--dry_run` to preview the activation without making changes.

* `diff <config1_name> <config2_name>`: Compares the contents of two configuration sets and shows the differences.

* `rename <old_name> <new_name>`: Renames the specified configuration set.

* `pull [--name <name>]`: Pulls a configuration set from a remote source and saves it locally. Optionally, specify a local name using `--name`.

---

## Quick Start

**Install the tool**

   ```bash
   pip install whl-conf
   ```

## Commands
1. **List all configuration sets**

   ```bash
   whl-conf list
   ```

2. **Show details of a specific config**

   ```bash
   whl-conf info <config_name>
   ```

3. **Create a new config set from a template**

   ```bash
   whl-conf create <template_name> <new_config_name>
   ```

4. **Delete a specified config**

   ```bash
   whl-conf delete <config_name>
   ```

5. **Activate a specified config set**

   ```bash
   whl-conf activate <config_name> [--dry_run]
   ```

6. **Compare two config sets**

   ```bash
   whl-conf diff <config1_name> <config2_name>
   ```

7. **Rename a config set**

   ```bash
   whl-conf rename <old_name> <new_name>
   ```

8. **Pull a config set from a remote source**

   ```bash
   whl-conf pull [--name <name>]
   ```
---

For more detailed information and additional options, you can run:

```bash
whl-conf --help
whl-conf <command> --help
```
