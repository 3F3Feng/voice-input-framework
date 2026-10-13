"""
Voice Input Framework - 服务端版本号

客户端和服务端各有自己的版本号,各涨各的:

- **客户端**:`gui/src-tauri/Cargo.toml`。只在发版(打 tag)时涨,更新记录在 `CHANGELOG.md`。
- **服务端**(STT / LLM 服务、建环境脚本):`pyproject.toml` 的 `project.version`,就是这里读的
  那个。**每个改了服务端的 PR 都涨**,更新记录在 `CHANGELOG-server.md` —— 服务跑的是用户本机
  仓库里的代码,点「更新服务」随时会拉到 main 上的改动,不像客户端那样等发版。版本号跟着
  每次改动走,更新时才看得出这次到底换了什么(CI 里 `scripts/check_server_version.py` 拦着
  漏涨的)。

2.7.2 及更早两边共用一个版本号。那时客户端拿服务报的版本和自己的比,「服务比我旧」就提示
更新 —— 于是只改服务端的那些改动合进去,版本号纹丝不动,更新完也看不出换了什么;只改客户端
的发版,服务端的版本号也跟着白涨一回。

两边还得对得上的只剩一件事:客户端要求服务端**不低于**某个版本
(`gui/src-tauri/src/service_update.rs` 的 `MIN_SERVER_VERSION`)。客户端用了服务端新加的
东西时,把那个数调到加它的那一版。

服务在 `/health`(STT 还有 WS 的 `ready`)里报两个字段,值相同:

- `server_version`:新客户端读这个;
- `app_version`:2.7.2 及更早的客户端只认这个名字。它们拿它和自己的版本比,服务端的版本号
  只会往上走,所以它们看到的始终是「服务不比我旧」。

这里读 `pyproject.toml` 而不是 import `client`:`client` 包 import 时会去试着加载 GUI 依赖,
服务端环境里不一定有,也没必要为一个字符串拖进来。

只在启动时读一次:服务报的是**它正在跑的**那份代码的版本。`git pull` 之后没重启,仓库里的
文件已经是新的,进程里跑的还是旧的——这时报旧版本才是对的。
"""

import logging
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def read_server_version(path: Path = PYPROJECT) -> str | None:
    """读 `pyproject.toml` 的 `project.version`。读不到时返回 None,绝不抛异常。

    读不到(文件缺了、被改坏了)只意味着客户端会把这个服务当成「版本未知」、
    提示用户更新,不该因此让服务起不来。
    """
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("读取服务端版本号失败(%s): %s", path, e)
        return None
    version = data.get("project", {}).get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return None


SERVER_VERSION: str | None = read_server_version()
