import importlib.resources
import sys


def read_resource_content(module_name, resource_name):
    """Reads the content of a resource file with Python version compatibility."""
    if sys.version_info >= (3, 9):
        return importlib.resources.files(module_name).joinpath(resource_name).read_text(encoding='utf-8')
    else:
        with importlib.resources.open_text(module_name, resource_name, encoding='utf-8') as f:
            return f.read()
