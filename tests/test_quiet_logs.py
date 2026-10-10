"""成功的健康检查不进日志(shared/quiet_logs.py)。

客户端每一两秒问一次 /health;它只留服务日志的最后几百行,不滤掉的话每次听写的耗时那一行
一分钟不到就被挤出去了(在一台测试机上读日志时就是这样:满屏的 /health)。
"""

import logging

import pytest

from shared import quiet_logs


def access_record(path: str, status: int, method: str = "GET") -> logging.LogRecord:
    # uvicorn 的访问日志就是这个格式和这五个参数
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:50000", method, path, "1.1", status),
        None,
    )


@pytest.mark.parametrize(
    "path, status, kept",
    [
        ("/health", 200, False),
        ("/health?verbose=1", 200, False),
        ("/health", 503, True),  # 不健康正是要看的
        ("/health", 401, True),
        ("/llm/health", 200, True),  # 转发给 LLM 的那个不算,问得不频繁
        ("/transcribe", 200, True),
        ("/models", 200, True),
    ],
)
def test_only_successful_health_checks_are_dropped(path, status, kept):
    assert quiet_logs._DropHealthChecks().filter(access_record(path, status)) is kept


def test_records_it_does_not_understand_are_kept():
    odd = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0, "plain message", None, None
    )
    assert quiet_logs._DropHealthChecks().filter(odd) is True


def test_install_is_idempotent():
    access = logging.getLogger("uvicorn.access")
    before = list(access.filters)
    try:
        quiet_logs.install()
        quiet_logs.install()
        added = [f for f in access.filters if f not in before]
        assert len(added) <= 1
        assert any(isinstance(f, quiet_logs._DropHealthChecks) for f in access.filters)
    finally:
        access.filters[:] = before


def test_stt_request_log_skips_health_but_keeps_other_requests(monkeypatch, caplog):
    from fastapi.testclient import TestClient

    import services.stt_server as srv

    client = TestClient(srv.app)
    with caplog.at_level(logging.INFO, logger=srv.logger.name):
        assert client.get("/health").status_code == 200
        client.get("/models")
    messages = [r.getMessage() for r in caplog.records]
    assert not any("GET /health" in m for m in messages), messages
    assert any("GET /models" in m for m in messages), messages
