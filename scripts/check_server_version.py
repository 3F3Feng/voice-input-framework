#!/usr/bin/env python3
"""改了服务端的 PR 有没有涨服务端版本号、写更新记录。CI 在每个 PR 上跑。

服务端的版本号(`pyproject.toml` 的 `version`)每个改了服务端的 PR 都要涨,并在
`CHANGELOG-server.md` 最上面加一节 —— 用户点「更新服务」随时会拉到 main 上的改动,版本号
跟着每次改动走,更新时才看得出这次换了什么(来龙去脉见 `shared/app_version.py`)。

    python scripts/check_server_version.py origin/main

和 `<base>` 比:这条分支动了下面 `SERVER_PATHS` 里的文件,就要求版本号比 `<base>` 上的大、
`CHANGELOG-server.md` 的第一节是这个新版本号。没动服务端的分支只查格式。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHANGELOG = "CHANGELOG-server.md"

#: 服务端跑的代码、「更新服务」时会重跑的建环境脚本和它装的依赖。以这些开头的路径算服务端。
#: `pyproject.toml` 在里面:依赖写在那儿,改了它「更新服务」装出来的环境就不一样了。
#: 测试、文档、评测工具(tests/、docs/、tools/)不算:它们不改变服务的行为。
SERVER_PATHS = (
    "services/",
    "shared/",
    "server/",
    "scripts/setup-env.ps1",
    "scripts/setup-env.sh",
    "scripts/setup_stt_env.sh",
    "requirements",
    "pyproject.toml",
)

#: `## [2.8.0] - 2026-10-12`。客户端(gui/src-tauri/src/service_update.rs)按同一个格式读。
SECTION = re.compile(r"^## \[(\d+(?:\.\d+)*)\] - (\d{4}-\d{2}-\d{2})[ \t]*$", re.MULTILINE)


def is_server_path(path: str) -> bool:
    path = path.replace("\\", "/")
    return path.startswith(SERVER_PATHS) and not path.endswith(".md")


def parse_version(v: str | None) -> tuple[int, ...] | None:
    """`2.8.0` → (2, 8, 0)。认不出来是 None。"""
    if not v or not re.fullmatch(r"\d+(\.\d+)*", v.strip()):
        return None
    return tuple(int(p) for p in v.strip().split("."))


def project_version(pyproject_text: str) -> str | None:
    try:
        version = tomllib.loads(pyproject_text).get("project", {}).get("version")
    except tomllib.TOMLDecodeError:
        return None
    return version.strip() if isinstance(version, str) and version.strip() else None


def changelog_sections(text: str) -> list[tuple[str, str, str]]:
    """更新记录里的每一节:(版本号, 日期, 正文),按文件里的先后(新的在前)。"""
    marks = list(SECTION.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group(1), m.group(2), text[m.end() : end].strip()))
    return out


def format_problems(version: str | None, changelog: str) -> list[str]:
    """不看改了什么都该成立的:版本号认得出、更新记录的第一节就是它、各节从新到旧。"""
    if parse_version(version) is None:
        return [f"pyproject.toml 的 version 认不出来:{version!r}(要写成 2.8.0 这样)"]
    sections = changelog_sections(changelog)
    if not sections:
        return [f"{CHANGELOG} 里没有 `## [版本号] - 日期` 这样的一节"]
    problems = []
    if sections[0][0] != version:
        problems.append(
            f"{CHANGELOG} 的第一节是 [{sections[0][0]}],pyproject.toml 的 version 是 {version}:"
            "涨了版本号就在最上面加一节,两处要一样"
        )
    versions = [parse_version(v) for v, _, _ in sections]
    if versions != sorted(versions, reverse=True) or len(set(versions)) != len(versions):
        problems.append(f"{CHANGELOG} 的各节要从新到旧排、版本号不重复")
    problems += [f"{CHANGELOG} 的 [{v}] 一节是空的" for v, _, body in sections if not body]
    return problems


def bump_problems(changed: list[str], base_version: str | None, version: str | None) -> list[str]:
    """这条分支动了服务端,版本号就得比 base 上的大。"""
    touched = sorted(p for p in changed if is_server_path(p))
    if not touched:
        return []
    base, head = parse_version(base_version), parse_version(version)
    if base is None or head is None or head > base:
        # base 上的认不出来(老分支、文件还没有):没法比,只能放过。
        return []
    shown = "、".join(touched[:5]) + (f" 等 {len(touched)} 个文件" if len(touched) > 5 else "")
    return [
        f"这条分支改了服务端({shown}),但 pyproject.toml 的 version 还是 {version}"
        f"(base 上是 {base_version})。把它涨一位(修 bug 涨第三位,加功能或改了行为涨第二位),"
        f"并在 {CHANGELOG} 最上面加一节 `## [新版本号] - 日期` 写清楚改了什么。"
    ]


def git(*args: str) -> str | None:
    out = subprocess.run(["git", *args], cwd=REPO, capture_output=True)
    return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    version = project_version((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    changelog_path = REPO / CHANGELOG
    changelog = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else ""
    problems = format_problems(version, changelog)

    # 三个点:只看这条分支自己的改动,base 后来合进去的别的东西不算。
    diff = git("diff", "--name-only", f"{base}...HEAD")
    if diff is None:
        print(f"::error::和 {base} 比不了(没有这个引用?checkout 时要带 fetch-depth: 0)")
        return 1
    changed = [ln.strip() for ln in diff.splitlines() if ln.strip()]
    base_pyproject = git("show", f"{base}:pyproject.toml")
    base_version = project_version(base_pyproject) if base_pyproject else None
    problems += bump_problems(changed, base_version, version)

    for p in problems:
        print(f"::error::{p}")
    if not problems:
        touched = sum(is_server_path(p) for p in changed)
        note = f"改了 {touched} 个服务端文件" if touched else "没有动服务端"
        print(f"服务端版本号 {version}(base:{base_version}),{note},没有问题。")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
