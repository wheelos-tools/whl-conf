import os
import functools
import inspect
import logging
from pathlib import Path
from typing import Any, Callable, TypeVar, Union

from filelock import FileLock, Timeout


class LockError(Exception):
    """Base class for lock-related errors."""
    pass


class LockAcquisitionTimeoutError(LockError):
    """Raised when lock acquisition times out."""

    def __init__(self, lock_file: str, timeout: float):
        message = f"Timeout ({timeout}s) occurred while trying to acquire lock: {lock_file}"
        super().__init__(message)
        self.lock_file = lock_file
        self.timeout = timeout


class ConfsLock:
    """
    A robust, cross-platform context manager lock for directory operations.
    """

    def __init__(self, directory_to_lock: Path, timeout: float = 10.0, lock_file_name: str = ".dir.lock"):
        if not isinstance(directory_to_lock, Path):
            raise TypeError("directory_to_lock must be a Path object")

        self.directory_to_lock = directory_to_lock
        self.timeout = timeout
        self.lock_file_path = self.directory_to_lock.resolve() / lock_file_name
        self._lock: Union[FileLock, None] = None
        self.logger = logging.getLogger(__name__)

    def __enter__(self):
        self.logger.debug(f"Attempting to acquire lock: {self.lock_file_path}")
        try:
            self.directory_to_lock.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise LockError(
                f"Failed to create lock directory {self.directory_to_lock}: {e}") from e

        self._lock = FileLock(str(self.lock_file_path), timeout=self.timeout)

        try:
            self._lock.acquire()
            self.logger.debug(f"Lock acquired: {self.lock_file_path}")
        except Timeout:
            raise LockAcquisitionTimeoutError(
                str(self.lock_file_path), self.timeout)
        except Exception as e:
            self.logger.error(
                f"An unexpected error occurred while acquiring lock: {e}", exc_info=True)
            raise LockError(
                f"Failed to acquire lock due to an unexpected error: {e}") from e
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._lock and self._lock.is_locked:
            self._lock.release()
            self.logger.debug(f"Lock released: {self.lock_file_path}")

        # Optional cleanup of the lock file
        try:
            if os.path.exists(self.lock_file_path):
                os.remove(self.lock_file_path)
        except OSError:
            pass
        return False


# Generic TypeVar for return types.
T = TypeVar("T")


def attribute_lock(attr_name: str, timeout: float = 10.0) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator factory to lock a directory path stored in an instance attribute.
    This is a more generic version of `global_dir_lock`.
    Args:
        attr_name (str): The name of the attribute on the instance (`self`) that
                         holds the Path object of the directory to lock.
        timeout (float): Lock acquisition timeout in seconds.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            # First argument is always the instance `self` for instance methods
            if not args:
                raise TypeError(
                    f"Locking decorator expects '{func.__name__}' to be a bound method.")

            instance: Any = args[0]
            if not hasattr(instance, attr_name):
                raise AttributeError(
                    f"Instance of '{type(instance).__name__}' is missing required "
                    f"locking attribute '{attr_name}' for method '{func.__name__}'."
                )

            lock_dir = getattr(instance, attr_name)
            if not isinstance(lock_dir, Path):
                raise TypeError(
                    f"Attribute '{attr_name}' must be a Path object, but got {type(lock_dir)}.")

            try:
                with ConfsLock(lock_dir, timeout=timeout):
                    return func(*args, **kwargs)
            except LockAcquisitionTimeoutError:
                raise
            except LockError as e:
                # Re-raise with more context for better diagnostics
                raise LockError(
                    f"A lock error occurred during operation '{func.__name__}': {e}") from e
        return wrapper
    return decorator


def method_call_lock(method_name: str, param_name: str, timeout: float = 10.0) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator factory to lock a directory resolved by an instance method call.
    This is a more generic and robust version of `config_dir_lock`.
    Args:
        method_name (str): The name of the method on `self` to call to get the
                           directory Path (e.g., '_get_config_path').
        param_name (str): The name of the parameter in the decorated function's
                          signature that contains the key for the method call.
        timeout (float): Lock acquisition timeout in seconds.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            if not args:
                raise TypeError(
                    f"Locking decorator expects '{func.__name__}' to be a bound method.")

            instance: Any = args[0]
            if not hasattr(instance, method_name) or not callable(getattr(instance, method_name)):
                raise AttributeError(
                    f"Instance of '{type(instance).__name__}' is missing required "
                    f"locking method '{method_name}' for function '{func.__name__}'."
                )

            # Use inspect to robustly find the parameter value
            sig = inspect.signature(func)
            bound_args = sig.bind(*args, **kwargs)
            bound_args.apply_defaults()

            if param_name not in bound_args.arguments:
                raise ValueError(
                    f"Locking decorator for '{func.__name__}' could not find required "
                    f"parameter '{param_name}' in the supplied arguments."
                )

            lookup_key = bound_args.arguments[param_name]

            # Call the instance method to get the directory to lock
            resolver_method = getattr(instance, method_name)
            lock_dir = resolver_method(lookup_key)

            if not isinstance(lock_dir, Path):
                raise TypeError(
                    f"Method '{method_name}' must return a Path object, but got {type(lock_dir)}.")

            try:
                with ConfsLock(lock_dir, timeout=timeout):
                    return func(*args, **kwargs)
            except LockError as e:
                raise LockError(
                    f"A lock error occurred during operation '{func.__name__}': {e}") from e
        return wrapper
    return decorator
