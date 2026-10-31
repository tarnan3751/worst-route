"""Jobs in real spawned worker processes: one run to its end, one cancelled, one killed; a worker
that dies partway through a message; a worker whose API process dies."""

import asyncio
import json
import os
import signal
import struct
import subprocess
import sys
import time
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from worst_route.api import jobs as jobs_module
from worst_route.api.jobs import _ProcessWorker
from worst_route.api.worker import JobSpec

from ..engine.graphs import lattice
from .support import GRID, collect, loaded, manager, request, save_build, submit, until

pytestmark = pytest.mark.slow


@pytest.mark.anyio
async def test_jobs_run_cancel_and_die_in_spawned_processes(data: Path) -> None:
    huge = lattice(40, 40)  # a run of a few seconds
    save_build(data, huge, "huge", latest=False)
    async with manager(data, inline_workers=False) as m:
        job = submit(m, loaded(data), request(GRID, [0, 45, 99], max_iterations=300))
        assert isinstance(job.worker, _ProcessWorker)
        events = await collect(m.subscribe(job))
        assert [e.name for e in events[:3]] == ["snapped", "baseline", "snapshot"]
        assert events[-1].name == "done" and "progress" in {e.name for e in events}
        assert m.store.get(json.loads(events[-1].data)["route_id"]) is not None
        worker = job.worker
        await until(lambda: not worker.alive(), timeout=10)  # joined after `done`

        build = loaded(data, "huge")
        long = request(huge, [0, 1599], max_iterations=1_000_000)
        cancelled = submit(m, build, long)
        subscription = m.subscribe(cancelled)
        await until(lambda: cancelled.accepted >= 1)
        await m.cancel(cancelled)
        events = await collect(subscription)
        assert events[-1].name == "done"
        assert json.loads(events[-1].data)["stopped_because"] == "cancelled"
        assert cancelled.worker is not None and not cancelled.worker.alive()  # terminated

        killed = submit(m, build, long)
        await until(lambda: killed.route is not None)
        assert isinstance(killed.worker, _ProcessWorker)
        os.kill(killed.worker._process.pid or 0, signal.SIGKILL)
        await until(lambda: killed.end is not None)
        assert killed.error is not None and killed.error.code == "WORKER_FAILED"
        late = await collect(m.subscribe(killed))
        ids = [e.id for e in late]
        assert ids == sorted(set(ids)) and late[-1].name == "error" and ids[-1] == killed.last_id


def mid_write_then_die(spec: JobSpec, conn: Connection) -> None:
    """A worker that starts a 2 MB message (its header says so), writes 60 kB of it and dies."""
    os.write(conn.fileno(), struct.pack("!i", 2_000_000) + b"x" * 60_000)
    os.kill(os.getpid(), signal.SIGKILL)


@pytest.mark.anyio
async def test_a_worker_dead_partway_through_a_message_fails_its_job_and_shutdown_completes(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs_module, "run_in_process", mid_write_then_die)
    async with manager(data, inline_workers=False) as m:
        job = submit(m, loaded(data), request(GRID, [0, 99]))
        events = await collect(m.subscribe(job), timeout=20)
        assert [e.name for e in events] == ["snapped", "error"]
        assert json.loads(events[-1].data)["code"] == "WORKER_FAILED"
        await asyncio.wait_for(m.stop(), 10)  # no read is left waiting on the half message


PROBE = """
import sys, time
from pathlib import Path
from worst_route.api.jobs import _ProcessWorker
from worst_route.api.worker import JobSpec, Started
from worst_route.engine.request import RouteRequest

if __name__ == "__main__":
    request = RouteRequest.model_validate_json(sys.argv[2])
    spec = JobSpec(Path(sys.argv[1]), "portland", "vast", "python", request)
    worker = _ProcessWorker(spec, "probe")
    assert isinstance(worker.get(60), Started)
    print(worker._process.pid, flush=True)
    time.sleep(120)
"""


def test_a_worker_exits_when_its_api_process_dies(data: Path) -> None:
    vast = lattice(80, 80)  # about 6 s of splices corner to corner
    save_build(data, vast, "vast", latest=False)
    req = request(vast, [0, 6399], max_iterations=1_000_000)
    root = Path(__file__).resolve().parents[2]
    api = subprocess.Popen(
        [sys.executable, "-c", PROBE, str(data), req.model_dump_json()],
        stdout=subprocess.PIPE,
        text=True,
        cwd=root,
    )
    assert api.stdout is not None
    pid = int(api.stdout.readline())  # its worker has started searching
    api.kill()
    api.wait()
    killed = time.monotonic()
    while time.monotonic() - killed < 3:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(pid, signal.SIGKILL)
    pytest.fail("the worker went on searching after its API process died")
