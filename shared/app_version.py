"""
Voice Input Framework - 项目版本号(服务端这一侧)

应用内更新只更新桌面客户端;STT / LLM 服务跑的是用户本机仓库里的代码。客户端
更新了、仓库没 `git pull`,新功能就会对着老服务端悄无声息地失效。为了让客户端
能看出「服务比我旧」,两个服务在 `/health` 里报 `app_version`。

版本号的唯一来源是 `gui/src-tauri/Cargo.toml`,发版时 `pyproject.toml` 同步改
(发版流水线会核对)。这里读 `pyproject.toml` 而不是 import `client`:`client`
包 import 时会去试着加载 GUI 依赖,服务端环境里不一定有,也没必要为一个字符串
拖进来。

只在启动时读一次:服务报的是**它正在跑的**那份代码的版本。`git pull` 之后没
重启,仓库里的文件已经是新的,进程里跑的还是旧的——这时报旧版本才是对的。
"""

import logging
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def read_app_version(path: Path = PYPROJECT) -> str | None:
    """读 `pyproject.toml` 的 `project.version`。读不到时返回 None,绝不抛异常。

    读不到(文件缺了、被改坏了)只意味着客户端会把这个服务当成「版本未知」、
    提示用户更新,不该因此让服务起不来。
    """
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("读取项目版本号失败(%s): %s", path, e)
        return None
    version = data.get("project", {}).get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return None


APP_VERSION: str | None = read_app_version()
