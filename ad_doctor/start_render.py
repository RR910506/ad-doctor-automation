from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    base_environment = os.environ.copy()
    base_environment["PYTHONUNBUFFERED"] = "1"

    web_environment = base_environment.copy()
    web_environment["AD_DOCTOR_APP_SCHEDULER_ENABLED"] = "0"
    web_environment["AD_DOCTOR_SCHEDULER_MODE"] = "worker"

    worker_environment = base_environment.copy()
    worker_environment["AD_DOCTOR_APP_SCHEDULER_ENABLED"] = "0"
    worker_environment["AD_DOCTOR_SCHEDULER_MODE"] = "worker"

    port = base_environment.get("PORT", "8501")
    web_command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "app.py",
        "--server.address",
        "0.0.0.0",
        "--server.port",
        port,
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    worker_command = [sys.executable, "-m", "src.scheduler_worker"]

    web_process = subprocess.Popen(web_command, env=web_environment)
    worker_process = subprocess.Popen(worker_command, env=worker_environment)
    stopping = False

    def stop_children(_signum=None, _frame=None) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        terminate_process(worker_process)
        terminate_process(web_process)

    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)

    try:
        while True:
            web_result = web_process.poll()
            if web_result is not None:
                stop_children()
                return int(web_result)
            worker_result = worker_process.poll()
            if worker_result is not None:
                worker_process = subprocess.Popen(worker_command, env=worker_environment)
            time.sleep(2)
    finally:
        stop_children()


def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
