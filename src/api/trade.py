"""
Trade API loader.

The implementation is distributed as a platform-specific compiled extension.
Run setup_environment.py to install the matching wheel before starting DesicAI.
"""

try:
    from ._trade import TradeAPI
except ImportError as exc:
    raise RuntimeError(
        "Trade 模块未安装或平台不匹配。请运行 python setup_environment.py "
        "自动安装当前系统和 Python 版本匹配的加密交易模块。"
    ) from exc


__all__ = ["TradeAPI"]
