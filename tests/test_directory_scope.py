"""Filesystem/provider integration using temporary markers and an HTTP mock only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from client import AiMemoryClient
from config import AiMemoryConfig, get_config_schema, load_config, save_config
from provider import AiMemoryProvider

from tests.conftest import mcp_response


@pytest.fixture
def http_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AiMemoryProvider, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/handoff":
            return httpx.Response(404)
        if request.url.path == "/api/v1/search":
            return httpx.Response(200, json=[{"snippet": "mock recall"}])
        if request.url.path == "/mcp":
            payload = json.loads(request.content)
            if payload["params"]["name"] == "memory_write_page":
                arguments = payload["params"]["arguments"]
                return mcp_response(
                    {"page_id": "page-1", "path": arguments["path"]}
                )
        return httpx.Response(200, json={"ok": True})

    def factory(config: AiMemoryConfig) -> AiMemoryClient:
        client = AiMemoryClient(config)
        client._transport = httpx.MockTransport(handler)
        return client

    monkeypatch.setattr("provider.AiMemoryClient", factory)
    return AiMemoryProvider(), requests


@pytest.mark.parametrize("mode", ["global", "project"])
def test_search_scope_config_roundtrip(tmp_path: Path, mode: str) -> None:
    config = AiMemoryConfig(search_scope=mode)
    assert config.search_scope == mode
    save_config({"search_scope": mode}, str(tmp_path))
    assert load_config(str(tmp_path)).search_scope == mode
    field = next(item for item in get_config_schema() if item["key"] == "search_scope")
    assert field["default"] == "project"
    assert set(field["choices"]) == {"global", "project"}


def test_project_search_is_the_default(
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
) -> None:
    provider, requests = http_provider
    assert AiMemoryConfig().search_scope == "project"
    result = provider._search({"query": "shared"})
    assert result == {
        "ok": False,
        "error": "Project scope is unknown",
        "results": [],
    }
    assert not requests


@pytest.mark.parametrize("invalid", ["", "invalid", None])
def test_invalid_search_scope_is_rejected_without_writing(tmp_path: Path, invalid: Any) -> None:
    with pytest.raises(ValueError, match="search_scope"):
        AiMemoryConfig(search_scope=invalid)
    with pytest.raises(ValueError, match="search_scope"):
        save_config({"search_scope": invalid}, str(tmp_path))
    assert not (tmp_path / "ai-memory.json").exists()


@pytest.mark.parametrize("directory", ["absent", "missing", "file", "unmarked", "relative"])
def test_unknown_scope_blocks_every_write_and_handoff(
    tmp_path: Path,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
    directory: str,
) -> None:
    provider, requests = http_provider
    # A process cwd marker must never become the session's implicit scope.
    repo = tmp_path / "imperio_2026"
    repo.mkdir()
    (repo / ".ai-memory.toml").write_text('workspace = "ws"\nproject = "root"\n')
    path = repo / "nonexistent"
    if directory == "absent":
        cwd = ""
    elif directory == "file":
        path = repo / "file.py"
        path.write_text("pass\n")
        cwd = str(path)
    elif directory == "unmarked":
        path = tmp_path / "unmarked"
        path.mkdir()
        cwd = str(path)
    elif directory == "relative":
        cwd = "imperio_2026"
    else:
        cwd = str(path)
    provider.initialize("unknown", cwd=cwd)
    result = provider._write({"path": "note.md", "body": "private"})
    assert result["ok"] is False
    assert "scope" in result["error"]
    provider.on_memory_write("add", "memory", "private")
    provider.sync_turn("private", "answer")
    provider.on_session_end([])
    assert not requests


def test_reinitialize_does_not_reuse_previous_marker_project(
    tmp_path: Path,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
) -> None:
    provider, requests = http_provider
    repo = tmp_path / "imperio_2026"
    repo.mkdir()
    (repo / ".ai-memory.toml").write_text('workspace = "ws"\nproject = "imperio_2026"\n')
    provider.initialize("first", cwd=str(repo))
    assert provider._write({"path": "note.md", "body": "first"})["ok"]
    requests.clear()
    provider.initialize("second", cwd="")
    assert not provider._write({"path": "note.md", "body": "must not leak"})["ok"]
    assert not requests


@pytest.mark.parametrize("source", ["file", "kwargs"])
def test_explicit_default_project_allows_writes_without_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
    source: str,
) -> None:
    provider, requests = http_provider
    kwargs: dict[str, Any] = {}
    if source == "file":
        (tmp_path / "ai-memory.json").write_text(json.dumps({"project": "hermes-default"}))
        kwargs["hermes_home"] = str(tmp_path)
    else:
        kwargs["project"] = "hermes-default"
    provider.initialize("explicit", **kwargs)
    assert provider._write({"path": "note.md", "body": "allowed"})["ok"]
    payload = json.loads(requests[-1].content)["params"]["arguments"]
    assert payload["project"] == "hermes-default"
    assert payload["workspace"] == "hermes"


@pytest.fixture
def monorepo(tmp_path: Path) -> Path:
    root = tmp_path / "imperio_2026"
    app = root / "apps" / "socorro_financeiro"
    (app / "src").mkdir(parents=True)
    (root / "src").mkdir()
    (root / ".ai-memory.toml").write_text(
        'workspace = "imperio"\nproject = "imperio_2026"\n'
    )
    (app / ".ai-memory.toml").write_text(
        'workspace = "financeiro"\nproject = "socorro_financeiro"\n'
    )
    return root


@pytest.mark.parametrize(
    "directory,workspace,project",
    [
        (".", "imperio", "imperio_2026"),
        ("src", "imperio", "imperio_2026"),
        ("apps/socorro_financeiro", "financeiro", "socorro_financeiro"),
        ("apps/socorro_financeiro/src", "financeiro", "socorro_financeiro"),
    ],
)
def test_nearest_toml_routes_search_writes_handoff_and_hooks(
    tmp_path: Path,
    monorepo: Path,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
    directory: str,
    workspace: str,
    project: str,
) -> None:
    provider, requests = http_provider
    home = tmp_path / "profile"
    save_config({"search_scope": "project", "lifecycle_capture": True}, str(home))
    cwd = str(monorepo / directory)
    provider.initialize("session", cwd=cwd, hermes_home=str(home), agent_workspace="hermes")
    assert dict(requests[-1].url.params) == {
        "agent": "hermes", "cwd": cwd, "workspace": workspace, "project": project,
    }
    assert provider._search({"query": "private"})["ok"]
    assert dict(requests[-1].url.params) == {
        "q": "private", "limit": "5", "workspace": workspace, "project": project,
    }
    provider._write({"path": "note.md", "body": "test"})
    body = json.loads(requests[-1].content)["params"]["arguments"]
    assert (body["workspace"], body["project"]) == (workspace, project)
    provider.sync_turn("test prompt", "test answer")
    provider.on_session_end([])
    for request in requests[-2:]:
        params = dict(request.url.params)
        assert (params["workspace"], params["project"]) == (workspace, project)
        assert params["cwd"] == cwd
        assert params["session_id"] == "session"


@pytest.mark.parametrize("source", ["file", "kwargs"])
def test_explicit_scope_overrides_nested_toml(
    tmp_path: Path,
    monorepo: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
    source: str,
) -> None:
    provider, requests = http_provider
    home = tmp_path / "profile"
    save_config({"search_scope": "project"}, str(home))
    kwargs: dict[str, Any] = {"hermes_home": str(home)}
    if source == "file":
        save_config({"workspace": "override-ws", "project": "override-project"}, str(home))
    else:
        kwargs.update(ai_memory_workspace="override-ws", project="override-project")
    provider.initialize("session", cwd=str(monorepo / "apps/socorro_financeiro/src"), **kwargs)
    provider._search({"query": "private"})
    assert requests[-1].url.params["workspace"] == "override-ws"
    assert requests[-1].url.params["project"] == "override-project"
    provider._write({"path": "note.md", "body": "allowed"})
    payload = json.loads(requests[-1].content)["params"]["arguments"]
    assert payload["project"] == "override-project"


def test_project_search_does_not_fall_back_to_global_when_scope_is_unknown(
    tmp_path: Path,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
) -> None:
    provider, requests = http_provider
    save_config({"search_scope": "project"}, str(tmp_path))
    provider.initialize("unknown", hermes_home=str(tmp_path))
    result = provider._search({"query": "private"})
    assert result["ok"] is False
    assert result["results"] == []
    assert provider.prefetch("private") == ""
    provider.queue_prefetch("private")
    assert not requests


@pytest.mark.parametrize("mode", ["global", "project"])
def test_queue_prefetch_uses_configured_scope_and_real_cache_update(
    tmp_path: Path,
    monorepo: Path,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
    mode: str,
) -> None:
    provider, requests = http_provider
    save_config({"search_scope": mode}, str(tmp_path))
    provider.initialize("session", cwd=str(monorepo), hermes_home=str(tmp_path))
    provider.queue_prefetch("test")
    key = ("session", mode, "imperio", "imperio_2026", "test")
    assert key in provider._prefetch_cache
    params = dict(requests[-1].url.params)
    expected = {"q": "test", "limit": "3"}
    if mode == "project":
        expected.update(workspace="imperio", project="imperio_2026")
    assert params == expected
    count = len(requests)
    assert provider.prefetch("test") == "mock recall"
    assert len(requests) == count


def test_reinitialize_clears_completed_work_and_rebinds_scope(
    monorepo: Path,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
) -> None:
    provider, requests = http_provider
    provider.initialize("old", cwd=str(monorepo), ai_memory_server_url="http://old.invalid")
    provider.sync_turn("original", "answer")
    provider.on_session_end([])
    provider.queue_prefetch("old query")
    assert requests
    assert provider._prefetch_cache
    provider.initialize("new", cwd=str(monorepo / "apps/socorro_financeiro"))
    assert not provider._prefetch_cache
    assert provider._config.project == "socorro_financeiro"


def test_initialize_without_home_ignores_search_scope_environment(
    monorepo: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
) -> None:
    provider, requests = http_provider
    monkeypatch.setenv("AI_MEMORY_SEARCH_SCOPE", "global")
    provider.initialize("session", cwd=str(monorepo))
    provider._search({"query": "private"})
    assert requests[-1].url.params["project"] == "imperio_2026"


@pytest.mark.parametrize("marker", ['workspace = "ws"\n', 'project = ""\n'])
def test_incomplete_marker_does_not_authorize_default_project_writes(
    tmp_path: Path,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
    marker: str,
) -> None:
    provider, requests = http_provider
    (tmp_path / ".ai-memory.toml").write_text(marker)
    provider.initialize("incomplete", cwd=str(tmp_path), agent_identity="profile")
    assert not provider._write({"path": "note.md", "body": "private"})["ok"]
    assert not requests


def test_cache_is_not_reused_between_search_modes(
    tmp_path: Path,
    monorepo: Path,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
) -> None:
    provider, requests = http_provider
    provider._config.search_scope = "global"
    provider.initialize("session", cwd=str(monorepo))
    provider.queue_prefetch("query")
    provider._config.search_scope = "project"
    requests.clear()
    assert provider.prefetch("query") == "mock recall"
    assert requests[-1].url.params["project"] == "imperio_2026"


def test_session_switch_discards_late_cache_without_dynamic_scope_change(
    monorepo: Path,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
) -> None:
    provider, requests = http_provider
    provider.initialize("old", cwd=str(monorepo))
    provider.queue_prefetch("query")
    assert provider._prefetch_cache
    provider.on_session_switch("new", cwd=str(monorepo / "apps/socorro_financeiro"))
    assert not provider._prefetch_cache
    assert provider._config.project == "imperio_2026"
    provider._write({"path": "note.md", "body": "same construction scope"})
    payload = json.loads(requests[-1].content)["params"]["arguments"]
    assert payload["project"] == "imperio_2026"


def test_failed_reinitialize_cannot_write_to_previous_project(
    tmp_path: Path,
    monorepo: Path,
    http_provider: tuple[AiMemoryProvider, list[httpx.Request]],
) -> None:
    provider, requests = http_provider
    provider.initialize("old", cwd=str(monorepo))
    (tmp_path / "ai-memory.json").write_text(json.dumps({"search_scope": "invalid"}))
    requests.clear()
    with pytest.raises(ValueError, match="search_scope"):
        provider.initialize("new", hermes_home=str(tmp_path))
    assert not provider._write({"path": "note.md", "body": "must not leak"})["ok"]
    assert not requests
