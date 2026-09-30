"""Public data-directory lock interface."""
from .instance_lock import InstanceLock

RuntimeLock = InstanceLock

__all__ = ["RuntimeLock"]
