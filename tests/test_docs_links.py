"""文档的链接:docs/ 里的每一份都要能从两份 README 点到,文档里的相对链接都要指向存在的文件。

docs/ 里攒过几份没人链接、内容早已过时的文档(旧客户端的进度报告、去掉了的网关的设计)。
没有入口的文档没人看,也就没人发现它过时了。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
READMES = ["README.md", "README.en.md"]
DOCS = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("*.md"))
LINKED_FILES = READMES + DOCS + ["mobile/README.md"]

_LINK = re.compile(r"\]\(([^)\s]+)\)|(?:src|href)=\"([^\"]+)\"")


def _targets(path: str) -> list[str]:
    """文件里所有指向仓库内文件的链接(去掉锚点),写成相对仓库根的路径。"""
    text = (ROOT / path).read_text(encoding="utf-8")
    base = (ROOT / path).parent
    found = []
    for match in _LINK.finditer(text):
        target = (match.group(1) or match.group(2)).split("#")[0]
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        resolved = (base / target).resolve()
        try:
            found.append(resolved.relative_to(ROOT).as_posix())
        except ValueError:
            # 指到仓库外面(比如 GitHub 上的 ../../releases):不是文件链接
            continue
    return found


@pytest.mark.parametrize("readme", READMES)
def test_every_doc_is_linked_from_the_readme(readme):
    linked = set(_targets(readme))
    missing = [doc for doc in DOCS if doc not in linked]
    assert not missing, f"{readme} 里没有链接到:{missing}"


@pytest.mark.parametrize("path", LINKED_FILES)
def test_relative_links_point_at_files_that_exist(path):
    broken = [t for t in _targets(path) if not (ROOT / t).exists()]
    assert not broken, f"{path} 里的链接指向不存在的文件:{broken}"


def test_the_two_readmes_link_to_each_other():
    assert "README.en.md" in _targets("README.md")
    assert "README.md" in _targets("README.en.md")
