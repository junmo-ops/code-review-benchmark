"""Persisted child-process lifecycle shared by reviewer adapters."""
import codecs,json,os,signal,subprocess,sys,time
from concurrent.futures import CancelledError
from contextlib import ExitStack
from datetime import datetime,timezone
from .storage import save

def capture_process(command, folder, name, *, timeout=1800, stdout_suffix=".stdout.json", cwd=None, case_jobs=1, cancellation_requested=lambda: False, redact=str):
    """Persist child output as it arrives, including on timeout or Ctrl+C."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / (name + suffix) for suffix in
             (stdout_suffix, ".stderr.txt", ".execution.json", ".events.jsonl", ".outcome.json")]
    previous = [p for p in paths if p.exists()]
    if previous:
        archive = folder / "attempts" / f"{name}-{time.time_ns()}"
        archive.mkdir(parents=True)
        for path in previous:
            path.replace(archive / path.name)
    started = time.monotonic()
    record = {"status": "starting", "process_timeout_seconds": timeout,
              "command_without_credentials": [redact(v) for v in command],
              "started_at": datetime.now(timezone.utc).isoformat(),
              "python_version": sys.version.split()[0], "platform": sys.platform}
    save(paths[2], record)
    process = None
    with ExitStack() as stack:
        events = stack.enter_context(paths[3].open("w", encoding="utf-8"))
        last_output = started

        def event(kind, **details):
            events.write(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=round(time.monotonic()-started, 3), kind=kind, **details), ensure_ascii=False) + "\n")
            events.flush()

        event("starting", process_timeout_seconds=timeout)
        writers = [stack.enter_context(p.open("wb")) for p in paths[:2]]
        readers = [stack.enter_context(p.open("rb")) for p in paths[:2]]
        decoders = [codecs.getincrementaldecoder("utf-8")("replace") for _ in readers]

        def display(final=False):
            nonlocal last_output
            for reader, decoder, target in zip(readers, decoders, (sys.stdout, sys.stderr)):
                while data := reader.read(65536):
                    chunk = decoder.decode(data)
                    last_output = time.monotonic()
                    event("output", stream="stdout" if target is sys.stdout else "stderr", text=redact(chunk))
                    print(f"[{name}] {chunk}" if case_jobs > 1 else chunk, end="", file=target, flush=True)
                if final:
                    print(decoder.decode(b"", final=True), end="", file=target, flush=True)

        try:
            if cancellation_requested():
                raise CancelledError("批次已取消")
            options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                       else {"start_new_session": True})
            process = subprocess.Popen(command, cwd=cwd, stdout=writers[0], stderr=writers[1], **options)
            record.update(status="running", pid=process.pid)
            event("running", pid=process.pid)
            save(paths[2], record)
            heartbeat = started
            while process.poll() is None:
                display()
                if cancellation_requested():
                    raise CancelledError("批次已取消")
                now = time.monotonic()
                if now - started >= timeout:
                    raise subprocess.TimeoutExpired(command, timeout)
                if now - heartbeat >= 30:
                    print(f"\n[{name}] 仍在等待检视进程，已运行 {int(now-started)} 秒；日志：{folder}", flush=True)
                    event("heartbeat", seconds_since_output=round(now-last_output, 3))
                    heartbeat = now
                time.sleep(0.2)
            record["status"] = "exited"
        except subprocess.TimeoutExpired:
            record["status"] = "timed_out"
            raise
        except (KeyboardInterrupt, CancelledError):
            record["status"] = "interrupted"
            raise
        except BaseException:
            record["status"] = "failed"
            raise
        finally:
            try:
                if process is not None and process.poll() is None:
                    if os.name == "nt":
                        # Only terminate this invocation and its descendants.
                        cleanup = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                                 capture_output=True, timeout=15, check=False)
                        record["cleanup_exit_code"] = cleanup.returncode
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
            finally:
                record.update(exit_code=process.poll() if process is not None else None,
                              elapsed_seconds=round(time.monotonic()-started, 3))
                record["ended_at"] = datetime.now(timezone.utc).isoformat()
                save(paths[2], record)
                display(final=True)
                event("finished", status=record["status"], exit_code=record["exit_code"])
    return subprocess.CompletedProcess(command, process.returncode,
        paths[0].read_text(encoding="utf-8", errors="replace"),
        paths[1].read_text(encoding="utf-8", errors="replace"))

