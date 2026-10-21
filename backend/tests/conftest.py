import pytest
from fastapi.testclient import TestClient

from auditcore import server
from test_server import fake_result


@pytest.fixture(autouse=True)
def _no_network_research(monkeypatch):
    monkeypatch.setenv("AUDITCORE_LANDSCAPE", "off")  # tests must never call GitHub; they pass a fake search explicitly
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


@pytest.fixture
def client(tmp_path):
    calls = []
    contexts = []

    def ev(url, rubric, start=None, end=None, model=None, members=None, context=None):
        calls.append((url, members))
        contexts.append(context)
        if "boom" in url:
            raise RuntimeError("clone failed")
        copied = "copy" in url
        fps = [{"h": f"s{i}", "f": f"f{i}.py", "n": 40} for i in range(5)] if copied or "orig" in url else None
        flags = [{"severity": "high", "code": "bulk_commit", "message": "dump", "source": "commits"}] if "dump" in url else []
        return fake_result(url, rubric, start, end, members=members, context=context, score=50.0 if copied else 80.0, fps=fps, flags=flags)

    app = server.create_app(f"sqlite:///{tmp_path}/t.db", evaluator=ev, workers=1)
    c = TestClient(app)
    c.calls = calls
    c.contexts = contexts
    return c


@pytest.fixture(autouse=True)
def _no_scheduler(monkeypatch):
    monkeypatch.setenv("AUDITCORE_SCHEDULER", "off")  # tests call app.state.tick() themselves
