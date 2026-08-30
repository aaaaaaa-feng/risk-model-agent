"""冻结发行包的内部诊断边界。"""

from .self_test import run_package_self_test

__all__ = ["run_package_self_test"]
