"""Tests for worker runner resilience."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nanobot.worker.runner import WorkerRunner


class _DummyProvider:
    async def chat_with_retry(self, *args, **kwargs):
        raise AssertionError("chat_with_retry should not be called in these tests")


class _StubClient:
    def __init__(self, register_side_effects: list[object]) -> None:
        self._register_side_effects = list(register_side_effects)
        self.register_calls = 0
        self.unregister_calls = 0
        self.close_calls = 0
        self.claim_calls = 0

    async def claim_task(self, capabilities=None):
        self.claim_calls += 1
        return None

    async def heartbeat(self, current_task_id=None, status="online") -> dict:
        return {"ok": True}

    async def report_result(self, *args, **kwargs) -> None:
        return None

    async def report_progress(self, *args, **kwargs) -> None:
        return None

    async def register(self, name: str) -> dict:
        self.register_calls += 1
        item = self._register_side_effects.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def unregister(self) -> None:
        self.unregister_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_runner_retries_registration_until_success(tmp_path: Path, monkeypatch):
    client = _StubClient([
        RuntimeError("supervisor down"),
        RuntimeError("still down"),
        {"ok": True},
    ])
    runner = WorkerRunner(
        supervisor_url="http://test",
        worker_id="w-test",
        worker_name="worker",
        workspace=tmp_path,
        provider=_DummyProvider(),
        model="mock-model",
        poll_interval_s=0.01,
        supervisor_client=client,  # type: ignore[arg-type]
    )

    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def _fake_poll_loop() -> None:
        runner._running = False

    async def _fake_heartbeat_loop() -> None:
        while runner._running:
            await _fake_sleep(0)

    runner._wait_for_shutdown_or_timeout = _fake_sleep  # type: ignore[assignment]
    runner._poll_loop = _fake_poll_loop  # type: ignore[assignment]
    runner._heartbeat_loop = _fake_heartbeat_loop  # type: ignore[assignment]

    await runner.run()

    assert client.register_calls == 3
    assert sleeps == [0.01, 0.01]
    assert client.unregister_calls == 1
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_runner_drains_current_task_before_shutdown(tmp_path: Path) -> None:
    client = _StubClient([{"ok": True}])
    runner = WorkerRunner(
        supervisor_url="http://test",
        worker_id="w-drain",
        worker_name="worker",
        workspace=tmp_path,
        provider=_DummyProvider(),
        model="mock-model",
        poll_interval_s=0.01,
        drain_timeout_s=0.1,
        supervisor_client=client,  # type: ignore[arg-type]
    )

    task_started = asyncio.Event()
    allow_finish = asyncio.Event()
    task_finished = asyncio.Event()

    async def _claim_task(capabilities=None):
        if client.claim_calls == 0:
            client.claim_calls += 1
            return {"task_id": "task-1", "instruction": "do work"}
        client.claim_calls += 1
        return None

    async def _execute_task(task_data):
        runner._current_task_id = task_data["task_id"]
        task_started.set()
        try:
            await allow_finish.wait()
        finally:
            runner._current_task_id = None
            task_finished.set()

    client.claim_task = _claim_task  # type: ignore[method-assign]
    runner._execute_task = _execute_task  # type: ignore[assignment]

    run_task = asyncio.create_task(runner.run())
    await asyncio.wait_for(task_started.wait(), timeout=1.0)

    await runner.request_shutdown(reason="test shutdown")
    assert run_task.done() is False

    allow_finish.set()
    await asyncio.wait_for(task_finished.wait(), timeout=1.0)
    await asyncio.wait_for(run_task, timeout=1.0)

    assert client.unregister_calls == 1
    assert client.close_calls == 1
    assert client.claim_calls == 1


@pytest.mark.asyncio
async def test_runner_force_interrupts_task_after_drain_timeout(tmp_path: Path) -> None:
    client = _StubClient([{"ok": True}])
    runner = WorkerRunner(
        supervisor_url="http://test",
        worker_id="w-timeout",
        worker_name="worker",
        workspace=tmp_path,
        provider=_DummyProvider(),
        model="mock-model",
        poll_interval_s=0.01,
        drain_timeout_s=0.01,
        supervisor_client=client,  # type: ignore[arg-type]
    )

    task_started = asyncio.Event()
    task_cancelled = asyncio.Event()
    blocker = asyncio.Event()

    async def _claim_task(capabilities=None):
        if client.claim_calls == 0:
            client.claim_calls += 1
            return {"task_id": "task-2", "instruction": "never finish"}
        client.claim_calls += 1
        return None

    async def _execute_task(task_data):
        runner._current_task_id = task_data["task_id"]
        task_started.set()
        try:
            await blocker.wait()
        except asyncio.CancelledError:
            task_cancelled.set()
            raise
        finally:
            runner._current_task_id = None

    client.claim_task = _claim_task  # type: ignore[method-assign]
    runner._execute_task = _execute_task  # type: ignore[assignment]

    run_task = asyncio.create_task(runner.run())
    await asyncio.wait_for(task_started.wait(), timeout=1.0)

    await runner.request_shutdown(reason="test timeout")
    await asyncio.wait_for(task_cancelled.wait(), timeout=1.0)
    await asyncio.wait_for(run_task, timeout=1.0)

    assert client.unregister_calls == 1
    assert client.close_calls == 1