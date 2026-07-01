from __future__ import annotations

from contextlib import closing
import os
import signal
import socket
import threading
import time

from src.app_scheduler import (
    claim_next_queued_run,
    queue_due_runs,
    record_worker_heartbeat,
    requeue_interrupted_worker_runs,
    run_claimed_schedule,
)
from src.automation_engine import get_automation_settings
from src.config import DATABASE_PATH
from src.db import get_connection, initialize_database


DEFAULT_POLL_SECONDS = 30


def main() -> int:
    initialize_database(DATABASE_PATH)
    worker_id = os.getenv("RENDER_INSTANCE_ID") or f"{socket.gethostname()}:{os.getpid()}"
    poll_seconds = max(
        10,
        int(os.getenv("AD_DOCTOR_SCHEDULER_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))),
    )
    stop_event = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    recovered = requeue_interrupted_worker_runs(DATABASE_PATH)
    record_worker_heartbeat(
        "idle",
        f"Cloud scheduler started. {recovered} interrupted run(s) returned to the queue.",
        worker_id=worker_id,
        database_path=DATABASE_PATH,
    )

    dispatcher = threading.Thread(
        target=dispatcher_loop,
        kwargs={
            "stop_event": stop_event,
            "poll_seconds": poll_seconds,
            "worker_id": worker_id,
        },
        name="ad-doctor-schedule-dispatcher",
        daemon=True,
    )
    dispatcher.start()

    while not stop_event.is_set():
        claimed = claim_next_queued_run(DATABASE_PATH)
        if not claimed:
            stop_event.wait(2)
            continue
        schedule_key, schedule_time = claimed
        settings = get_automation_settings(DATABASE_PATH)
        run_claimed_schedule(
            schedule_key=schedule_key,
            schedule_time=schedule_time,
            settings=settings,
            database_path=DATABASE_PATH,
        )

    record_worker_heartbeat(
        "stopped",
        "Cloud scheduler stopped cleanly.",
        worker_id=worker_id,
        database_path=DATABASE_PATH,
    )
    return 0


def dispatcher_loop(
    *,
    stop_event: threading.Event,
    poll_seconds: int,
    worker_id: str,
) -> None:
    while not stop_event.is_set():
        try:
            queued = queue_due_runs(database_path=DATABASE_PATH)
            status, message = describe_queue_state(queued)
            record_worker_heartbeat(
                status,
                message,
                worker_id=worker_id,
                database_path=DATABASE_PATH,
            )
        except Exception as exc:
            record_worker_heartbeat(
                "error",
                f"Scheduler check failed: {exc}",
                worker_id=worker_id,
                database_path=DATABASE_PATH,
            )
        stop_event.wait(poll_seconds)


def describe_queue_state(newly_queued: list[str]) -> tuple[str, str]:
    with closing(get_connection(DATABASE_PATH)) as connection:
        running = int(
            connection.execute(
                "SELECT COUNT(*) FROM automation_run_history WHERE status = 'running'"
            ).fetchone()[0]
        )
        queued = int(
            connection.execute(
                "SELECT COUNT(*) FROM automation_run_history WHERE status = 'queued'"
            ).fetchone()[0]
        )
    if running:
        return "running", f"Automation is running. {queued} timestamp(s) queued next."
    if queued:
        return "idle", f"{queued} timestamp(s) are queued for processing."
    if newly_queued:
        return "idle", f"Queued {len(newly_queued)} scheduled timestamp(s)."
    return "idle", "Cloud scheduler is active and waiting for the next timestamp."


if __name__ == "__main__":
    raise SystemExit(main())
