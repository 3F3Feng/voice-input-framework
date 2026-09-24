"""
服务端报项目版本号(`app_version`)。

客户端靠它看出「应用更新了、仓库里的服务还是旧的」,所以两件事要钉住:
版本号确实来自 pyproject.toml(和客户端同一个来源),以及 STT / LLM 的 /health、
STT 的 WS ready 消息里都带着它。
"""

import json
import sys
import tomllib
from pathlib import Path

project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

from shared import app_version  # noqa: E402


def _pyproject_version() -> str:
    with open(project_dir / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


class TestReadAppVersion:
    def test_matches_pyproject(self):
        assert app_version.APP_VERSION == _pyproject_version()
        assert app_version.read_app_version() == _pyproject_version()

    def test_client_package_is_in_sync(self):
        """发版时三处一起改;这里顺手核对 client/__init__.py,免得报出来的和客户端对不上。"""
        text = (project_dir / "client" / "__init__.py").read_text(encoding="utf-8")
        assert f'__version__ = "{_pyproject_version()}"' in text

    def test_missing_file_is_none_not_an_error(self, tmp_path):
        assert app_version.read_app_version(tmp_path / "nope.toml") is None

    def test_broken_toml_is_none(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text("[project\nversion = ", encoding="utf-8")
        assert app_version.read_app_version(p) is None

    def test_missing_or_blank_version_is_none(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text('[project]\nname = "x"\n', encoding="utf-8")
        assert app_version.read_app_version(p) is None
        p.write_text('[project]\nversion = "  "\n', encoding="utf-8")
        assert app_version.read_app_version(p) is None
        p.write_text('[project]\nversion = " 2.3.10 "\n', encoding="utf-8")
        assert app_version.read_app_version(p) == "2.3.10"


class TestHealthReportsAppVersion:
    def test_stt_health(self):
        from fastapi.testclient import TestClient

        import services.stt_server as srv

        body = TestClient(srv.app).get("/health").json()
        assert body["app_version"] == _pyproject_version()
        # 老客户端读的接口版本字段照旧在
        assert body["version"] == "1.1.0"

    def test_llm_health(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        import services.llm_server as srv

        monkeypatch.setattr(srv, "LLM_STATE_FILE", tmp_path / "llm_state.json")
        body = TestClient(srv.app).get("/health").json()
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
        assert ready["app_version"] == _pyproject_version()
