"""Queue worker: lease-based, crash-safe, multi-thread. drain() for tests/dev inline mode."""
import argparse
import threading
import time
import uuid

from packages.core import config, queue
from packages.core.db import init_db
from packages.core.loop import AgentLoop
from packages.core.release import check_guardrails

STOP = threading.Event()


def process_job(job: dict, loop: AgentLoop, worker_id: str) -> None:
    try:
        if job["kind"] == "agent_run":
            loop.run_ticket(job["ticket_id"], job["id"], worker_id)
        elif job["kind"] == "resume_approval":
            loop.resume_approval(job["ticket_id"], job["payload"]["approval_id"], job["id"], worker_id)
        else:
            raise ValueError(f"unknown job kind {job['kind']}")
        queue.finish(job["id"], "done")
    except Exception as exc:  # noqa: BLE001
        if job["attempts"] >= config.MAX_JOB_ATTEMPTS:
            queue.dead_letter(job["id"], str(exc))
        else:
            queue.finish(job["id"], "pending", str(exc))


def drain(loop: AgentLoop | None = None, max_jobs: int = 200) -> int:
    loop = loop or AgentLoop()
    wid = "drain-" + uuid.uuid4().hex[:6]
    n = 0
    while n < max_jobs:
        job = queue.claim(wid)
        if job is None:
            break
        process_job(job, loop, wid)
        n += 1
    return n


def poll(wid: str, loop: AgentLoop):
    while not STOP.is_set():
        job = queue.claim(wid)
        if job is None:
            try:
                check_guardrails(config.DEFAULT_SPEC)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)
            continue
        process_job(job, loop, wid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=config.WORKER_THREADS)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    init_db()
    loop = AgentLoop()
    if args.once:
        print(f"drained {drain(loop)} jobs")
        return
    threads = []
    for i in range(args.workers):
        wid = f"worker-{i}-{uuid.uuid4().hex[:6]}"
        t = threading.Thread(target=poll, args=(wid, loop), daemon=True)
        t.start()
        threads.append(t)
    print(f"worker up: {args.workers} threads", flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        STOP.set()


if __name__ == "__main__":
    main()