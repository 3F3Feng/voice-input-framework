"""
服务端版本号(`shared/app_version.py`)和它的规矩。

客户端和服务端各有自己的版本号。这里钉住几件事:
- 服务报的版本号确实来自 pyproject.toml;STT / LLM 的 /health、STT 的 WS ready 里都有,
  新名字 `server_version` 和给老客户端的老名字 `app_version` 值相同;
- `CHANGELOG-server.md` 的第一节就是当前版本(客户端按这个格式读更新记录);
- 客户端要求的最低服务端版本不能比服务端自己的版本还高;
- 「改了服务端就得涨版本号」那条 CI 检查(scripts/check_server_version.py)的判断规则。
"""

import importlib.util
import json
import re
import sys
import tomllib
from pathlib import Path

project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

from shared import app_version  # noqa: E402


def _load_check_script():
    spec = importlib.util.spec_from_file_location(
        "check_server_version", project_dir / "scripts" / "check_server_version.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_check_script()


def _pyproject_version() -> str:
    with open(project_dir / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def _client_version() -> str:
    """客户端版本号的唯一来源:gui/src-tauri/Cargo.toml。"""
    with open(project_dir / "gui" / "src-tauri" / "Cargo.toml", "rb") as f:
        return tomllib.load(f)["package"]["version"]


class TestReadServerVersion:
    def test_matches_pyproject(self):
        assert app_version.SERVER_VERSION == _pyproject_version()
        assert app_version.read_server_version() == _pyproject_version()

    def test_python_client_package_follows_the_client_version(self):
        """老的 Python 客户端跟着客户端的版本号走(发版流水线也核对),不是服务端的。"""
        text = (project_dir / "client" / "__init__.py").read_text(encoding="utf-8")
        assert f'__version__ = "{_client_version()}"' in text

    def test_missing_file_is_none_not_an_error(self, tmp_path):
        assert app_version.read_server_version(tmp_path / "nope.toml") is None

    def test_broken_toml_is_none(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text("[project\nversion = ", encoding="utf-8")
        assert app_version.read_server_version(p) is None

    def test_missing_or_blank_version_is_none(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text('[project]\nname = "x"\n', encoding="utf-8")
        assert app_version.read_server_version(p) is None
        p.write_text('[project]\nversion = "  "\n', encoding="utf-8")
        assert app_version.read_server_version(p) is None
        p.write_text('[project]\nversion = " 2.3.10 "\n', encoding="utf-8")
        assert app_version.read_server_version(p) == "2.3.10"


class TestHealthReportsServerVersion:
    """新名字给新客户端,老名字给 2.7.2 及更早的客户端,值相同。"""

    def test_stt_health(self):
        from fastapi.testclient import TestClient

        import services.stt_server as srv

        body = TestClient(srv.app).get("/health").json()
        assert body["server_version"] == _pyproject_version()
        assert body["app_version"] == _pyproject_version()
        # 老客户端读的接口版本字段照旧在
        assert body["version"] == "1.1.0"

    def test_llm_health(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        import services.llm_server as srv

        monkeypatch.setattr(srv, "LLM_STATE_FILE", tmp_path / "llm_state.json")
        body = TestClient(srv.app).get("/health").json()
        assert body["server_version"] == _pyproject_version()
        assert body["app_version"] == _pyproject_version()
        assert body["version"] == "1.0.0"

    def test_stt_ws_ready(self, monkeypatch):
        from fastapi.testclient import TestClient

        import services.stt_server as srv

        # ready 不该去问 LLM(见 test_stt_server 里 R29 那条),这里干脆关掉 LLM。
        monkeypatch.setattr(srv, "LLM_ENABLED", False)
        with TestClient(srv.app).websocket_connect("/ws/stream") as ws:
            ready = json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "end"}))
        assert ready["type"] == "ready"
        assert ready["server_version"] == _pyproject_version()
        assert ready["app_version"] == _pyproject_version()


class TestServerChangelog:
    def test_top_section_is_the_current_version(self):
        """涨了版本号没写更新记录(或者反过来),在这里就拦住,不用等到 CI 的那一步。"""
        text = (project_dir / check.CHANGELOG).read_text(encoding="utf-8")
        assert check.format_problems(_pyproject_version(), text) == []

    def test_clients_minimum_is_not_ahead_of_the_server(self):
        """客户端要求的最低服务端版本要是比服务端自己还高,更新到最新也永远在提示「服务太旧」。"""
        rust = (project_dir / "gui" / "src-tauri" / "src" / "service_update.rs").read_text(
            encoding="utf-8"
        )
        found = re.search(r'pub const MIN_SERVER_VERSION: &str = "([^"]+)";', rust)
        assert found, "service_update.rs 里找不到 MIN_SERVER_VERSION"
        minimum = check.parse_version(found.group(1))
        assert minimum is not None, found.group(1)
        assert minimum <= check.parse_version(_pyproject_version())


class TestCheckServerVersion:
    """scripts/check_server_version.py 的判断规则。"""

    def test_which_paths_count_as_server(self):
        for path in (
            "services/llm_server.py",
            "shared/i18n.py",
            "server/models/whisper.py",
            "scripts/setup-env.ps1",
            "scripts/setup-env.sh",
            "requirements-llm.txt",
            "pyproject.toml",
            "services\\stt_server.py",
        ):
            assert check.is_server_path(path), path
        for path in (
            "tests/test_llm_server.py",
            "tools/llm_dictation_eval.py",
            "docs/models.md",
            "gui/src/App.vue",
            "gui/src-tauri/src/service_update.rs",
            "CHANGELOG-server.md",
            "CHANGELOG.md",
            "scripts/generate_latest_json.py",
            "services/README.md",
            ".github/workflows/ci.yml",
        ):
            assert not check.is_server_path(path), path

    def test_versions_compare_as_numbers(self):
        assert check.parse_version("2.10.0") > check.parse_version("2.9.3")
        assert check.parse_version(" 2.8.0 ") == (2, 8, 0)
        for bad in (None, "", "v2.8.0", "2.8.0-rc1", "二点八"):
            assert check.parse_version(bad) is None

    def test_server_change_without_a_bump_is_reported(self):
        problems = check.bump_problems(["services/llm_server.py", "tests/x.py"], "2.8.0", "2.8.0")
        assert len(problems) == 1
        assert "services/llm_server.py" in problems[0] and "2.8.0" in problems[0]
        # 版本号往回走也不行
        assert check.bump_problems(["shared/i18n.py"], "2.8.1", "2.8.0")

    def test_server_change_with_a_bump_passes(self):
        assert check.bump_problems(["services/llm_server.py"], "2.8.0", "2.8.1") == []
        assert check.bump_problems(["services/llm_server.py"], "2.9.9", "2.10.0") == []

    def test_client_or_docs_only_changes_need_no_bump(self):
        changed = ["gui/src/App.vue", "docs/models.md", "tests/test_i18n.py", "CHANGELOG.md"]
        assert check.bump_problems(changed, "2.8.0", "2.8.0") == []

    def test_unreadable_base_version_does_not_block(self):
        """base 是个连 pyproject 都没有的老分支时没法比,不能因此把 PR 卡死。"""
        assert check.bump_problems(["services/llm_server.py"], None, "2.8.0") == []

    def test_sections_are_parsed_with_their_bodies(self):
        text = (
            "# 服务端更新记录\n\n说明。\n\n"
            "## [2.8.1] - 2026-10-20\n\n### Fixed\n\n- 修了一处\n\n"
            "## [2.8.0] - 2026-10-12\n\n- 第一版\n"
        )
        assert check.changelog_sections(text) == [
            ("2.8.1", "2026-10-20", "### Fixed\n\n- 修了一处"),
            ("2.8.0", "2026-10-12", "- 第一版"),
        ]
        assert check.format_problems("2.8.1", text) == []

    def test_format_problems(self):
        ok = "## [2.8.1] - 2026-10-20\n\n- a\n\n## [2.8.0] - 2026-10-12\n\n- b\n"
        # 涨了版本号没加一节
        assert any("第一节" in p for p in check.format_problems("2.8.2", ok))
        # 没有任何一节(标题没按格式写的也算没有)
        assert check.format_problems("2.8.0", "## 2.8.0\n\n- a\n")
        # 顺序反了、重复
        assert any(
            "从新到旧" in p
            for p in check.format_problems(
                "2.8.0", "## [2.8.0] - 2026-10-12\n\n- b\n\n## [2.8.1] - 2026-10-20\n\n- a\n"
            )
        )
        # 空的一节
        assert any(
            "空的" in p
            for p in check.format_problems(
                "2.8.1", "## [2.8.1] - 2026-10-20\n\n## [2.8.0] - 2026-10-12\n\n- b\n"
            )
        )
        # 版本号本身写坏了
        assert check.format_problems("2.8", ok) and check.format_problems("v2.8.1", ok)
