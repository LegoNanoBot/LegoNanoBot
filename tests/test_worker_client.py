"""Tests for worker client (against real supervisor API via TestClient)."""

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.worker.client import SupervisorClient


def _make_mock_response(json_data, status_code=200):
    """Create a mock that behaves like httpx.Response (sync .json())."""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://localhost/test")
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.asyncio
async def test_client_register():
    """Test the client sends well-formed registration request."""
    client = SupervisorClient("http://localhost:9200", "w-test")
    mock_response = _make_mock_response({
        "ok": True,
        "worker": {"worker_id": "w-test", "name": "tester", "status": "online"},
    })

    with patch.object(client._client, "request", AsyncMock(return_value=mock_response)) as mock_request:
        result = await client.register("tester", capabilities=["code"])
        mock_request.assert_called_once_with(
            "POST",
            "/api/v1/supervisor/workers/register",
            json={
                "worker_id": "w-test",
                "name": "tester",
                "capabilities": ["code"],
            },
        )
        assert result["ok"] is True

    await client.close()


@pytest.mark.asyncio
async def test_client_heartbeat():
    client = SupervisorClient("http://localhost:9200", "w-test")
    mock_response = _make_mock_response({"ok": True, "worker": {"status": "online"}})

    with patch.object(client._client, "request", AsyncMock(return_value=mock_response)) as mock_request:
        await client.heartbeat(current_task_id="t1", status="busy")
        mock_request.assert_called_once_with(
            "POST",
            "/api/v1/supervisor/workers/w-test/heartbeat",
            json={"current_task_id": "t1", "status": "busy"},
        )

    await client.close()


@pytest.mark.asyncio
async def test_client_claim_task_none():
    client = SupervisorClient("http://localhost:9200", "w-test")
    mock_response = _make_mock_response({"ok": True, "task": None})

    with patch.object(client._client, "request", AsyncMock(return_value=mock_response)):
        result = await client.claim_task()
        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_client_claim_task_found():
    client = SupervisorClient("http://localhost:9200", "w-test")
    mock_response = _make_mock_response({
        "ok": True,
        "task": {"task_id": "t1", "instruction": "do stuff"},
    })

    with patch.object(client._client, "request", AsyncMock(return_value=mock_response)):
        result = await client.claim_task()
        assert result is not None
        assert result["task_id"] == "t1"

    await client.close()


@pytest.mark.asyncio
async def test_client_create_task():
    client = SupervisorClient("http://localhost:9200", "w-test")
    mock_response = _make_mock_response({
        "ok": True,
        "task": {"task_id": "t-new", "instruction": "delegate"},
    })

    with patch.object(client._client, "request", AsyncMock(return_value=mock_response)) as mock_request:
        result = await client.create_task(instruction="delegate", label="delegated")
        assert result["task_id"] == "t-new"
        assert mock_request.call_args.args[0] == "POST"
        assert mock_request.call_args.args[1] == "/api/v1/supervisor/tasks"

    await client.close()


@pytest.mark.asyncio
async def test_client_get_task():
    client = SupervisorClient("http://localhost:9200", "w-test")
    mock_response = _make_mock_response({
        "task": {"task_id": "t1", "status": "running"},
    })

    with patch.object(client._client, "request", AsyncMock(return_value=mock_response)):
        result = await client.get_task("t1")
        assert result["status"] == "running"

    await client.close()


@pytest.mark.asyncio
async def test_client_wait_for_task_polls_until_terminal_state():
    client = SupervisorClient("http://localhost:9200", "w-test")
    client._sleep = AsyncMock()

    with patch.object(
        client,
        "get_task",
        AsyncMock(side_effect=[
            {"task_id": "t1", "status": "running"},
            {"task_id": "t1", "status": "completed", "result": "done"},
        ]),
    ):
        result = await client.wait_for_task("t1", poll_interval_s=0.01, timeout_s=1.0)

    assert result["result"] == "done"
    client._sleep.assert_awaited_once_with(0.01)

    await client.close()


@pytest.mark.asyncio
async def test_client_is_available_false_on_request_error():
    client = SupervisorClient("http://localhost:9200", "w-test")

    with patch.object(client._client, "request", AsyncMock(side_effect=httpx.ConnectError("down"))):
        assert await client.is_available() is False

    await client.close()


@pytest.mark.asyncio
async def test_client_report_progress():
    client = SupervisorClient("http://localhost:9200", "w-test")
    mock_response = _make_mock_response({"ok": True})

    with patch.object(client._client, "request", AsyncMock(return_value=mock_response)) as mock_request:
        await client.report_progress("t1", iteration=2, message="halfway")
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "/api/v1/supervisor/tasks/t1/progress"

    await client.close()


@pytest.mark.asyncio
async def test_client_report_result():
    client = SupervisorClient("http://localhost:9200", "w-test")
    mock_response = _make_mock_response({"ok": True})

    with patch.object(client._client, "request", AsyncMock(return_value=mock_response)) as mock_request:
        await client.report_result("t1", status="completed", result="done")
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[1]["json"]["status"] == "completed"
        assert call_args[1]["json"]["result"] == "done"

    await client.close()


@pytest.mark.asyncio
async def test_client_unregister_graceful():
    """Unregister should not raise even if the supervisor is unreachable."""
    client = SupervisorClient("http://localhost:9200", "w-test")

    with patch.object(client._client, "request", AsyncMock(side_effect=Exception("connection refused"))):
        await client.unregister()  # Should not raise

    await client.close()


@pytest.mark.asyncio
async def test_client_register_retries_transient_request_errors():
    client = SupervisorClient("http://localhost:9200", "w-test")
    client._sleep = AsyncMock()
    mock_response = _make_mock_response({
        "ok": True,
        "worker": {"worker_id": "w-test", "name": "tester", "status": "online"},
    })

    with patch.object(
        client._client,
        "request",
        AsyncMock(side_effect=[httpx.ConnectError("refused"), httpx.ReadError("reset"), mock_response]),
    ) as mock_request:
        result = await client.register("tester", capabilities=["code"])

    assert result["ok"] is True
    assert mock_request.await_count == 3
    assert client._sleep.await_count == 2

    await client.close()


@pytest.mark.asyncio
async def test_client_does_not_retry_non_retryable_http_errors():
    client = SupervisorClient("http://localhost:9200", "w-test")
    client._sleep = AsyncMock()

    with patch.object(
        client._client,
        "request",
        AsyncMock(side_effect=_make_http_status_error(404)),
    ) as mock_request:
        with pytest.raises(httpx.HTTPStatusError):
            await client.claim_task()

    assert mock_request.await_count == 1
    client._sleep.assert_not_awaited()

    await client.close()


@pytest.mark.asyncio
async def test_client_retries_retryable_http_errors():
    client = SupervisorClient("http://localhost:9200", "w-test")
    client._sleep = AsyncMock()
    mock_response = _make_mock_response({"ok": True, "task": None})

    with patch.object(
        client._client,
        "request",
        AsyncMock(side_effect=[_make_http_status_error(503), mock_response]),
    ) as mock_request:
        result = await client.claim_task()

    assert result is None
    assert mock_request.await_count == 2
    assert client._sleep.await_count == 1

    await client.close()
