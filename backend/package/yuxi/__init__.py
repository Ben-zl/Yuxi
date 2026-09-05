from dotenv import load_dotenv

load_dotenv(".env", override=True)

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

try:
    from importlib.metadata import version

    __version__ = version("yuxi")
except Exception:
    __version__ = "unknown"

executor = ThreadPoolExecutor()  # noqa: E402


def get_version():
    """Return the Yuxi version."""
    return __version__


def __getattr__(name):
    """按需暴露应用配置，避免轻量导入路径初始化重运行时。"""
    if name == "config":
        from yuxi.config.app import config

        # 导入子包会先把 ``yuxi.config`` 写回根包属性；恢复为 Config
        # 实例，避免知识库懒加载期间把它误解析为配置包模块。
        globals()["config"] = config
        return config
    raise AttributeError(name)
