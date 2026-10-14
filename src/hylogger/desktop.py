"""Windows desktop lifecycle: private PostgreSQL, API and resumable worker."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

from .runtime import command, hidden, root


def free_port(preferred: int) -> int:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".json.part")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


class Desktop:
    def __init__(self, data: Path, port: int = 8130):
        self.data = data.resolve()
        self.port = port
        self.children = []
        self.handles = []
        self.pg = None
        self.lock = None
        self.pg_started = False

    def pg_run(self, name: str, *args: str, check=True):
        # PyInstaller's DLL search directory must not leak into PostgreSQL.
        env = os.environ.copy()
        env["PATH"] = str(self.pg / "bin") + os.pathsep + env.get("PATH", "")
        if getattr(sys, "frozen", False):
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            # pg_ctl's server descendants can inherit pipe handles on Windows;
            # redirect to a real file so communicate() cannot wait on the server.
            with tempfile.TemporaryFile(mode="w+b") as output:
                result = subprocess.run(
                    [str(self.pg / "bin" / f"{name}.exe"), *args],
                    stdout=output,
                    stderr=output,
                    env=env,
                    timeout=120,
                    **hidden(),
                )
                output.seek(0)
                result.stdout = output.read().decode("utf-8", errors="replace")
                if check and result.returncode:
                    raise RuntimeError(f"{name} failed: {result.stdout}")
                return result
        finally:
            if getattr(sys, "frozen", False):
                ctypes.windll.kernel32.SetDllDirectoryW(str(root()))

    def spawn(self, module: str, *args: str):
        log = (self.data / "logs" / f"{module.rsplit('.', 1)[-1]}.log").open("a", encoding="utf-8")
        self.handles.append(log)
        process = subprocess.Popen(
            command(module, *args), stdout=log, stderr=log, cwd=root(), **hidden()
        )
        self.children.append(process)
        return process

    def start(self, status=print):
        import msvcrt

        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / "logs").mkdir(exist_ok=True)
        self.lock = (self.data / "desktop.lock").open("a+b")
        self.lock.seek(0)
        if not self.lock.read(1):
            self.lock.write(b"0")
            self.lock.flush()
        self.lock.seek(0)
        try:
            msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Explorer is already open for this data folder.") from error
        status("Preparing the included database runtime…")
        vendor = (
            root() / "vendor"
            if getattr(sys, "frozen", False)
            else root() / "build/desktop-resources/vendor"
        )
        archive = vendor / "postgres-runtime.zip"
        expected = json.loads((vendor / "runtime.json").read_text())["sha256"]
        self.pg = self.data / "runtime" / expected[:16]
        marker = self.pg / ".complete"
        if not marker.exists():
            if hashlib.file_digest(archive.open("rb"), "sha256").hexdigest() != expected:
                raise RuntimeError("Database runtime checksum mismatch")
            self.pg.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as package:
                for member in package.infolist():
                    if not (self.pg / member.filename).resolve().is_relative_to(self.pg.resolve()):
                        raise RuntimeError("Invalid runtime archive path")
                package.extractall(self.pg)
            marker.write_text(expected)
        config_path = self.data / "desktop.json"
        settings = (
            json.loads(config_path.read_text())
            if config_path.exists()
            else {"password": secrets.token_urlsafe(32), "db_port": free_port(55439)}
        )
        atomic_json(config_path, settings)
        pgdata = self.data / "postgres"
        if not (pgdata / "PG_VERSION").exists():
            status("Creating your local database…")
            password_file = self.data / ".pg-password"
            password_file.write_text(settings["password"], encoding="utf-8")
            try:
                self.pg_run(
                    "initdb",
                    "-D",
                    str(pgdata),
                    "-U",
                    "hylogger",
                    "--encoding=UTF8",
                    "--locale=C",
                    "--auth=scram-sha-256",
                    f"--pwfile={password_file}",
                )
            finally:
                password_file.unlink(missing_ok=True)
        running = self.pg_run("pg_ctl", "-D", str(pgdata), "status", check=False).returncode == 0
        if not running:
            settings["db_port"] = free_port(settings["db_port"])
            atomic_json(config_path, settings)
            self.pg_run(
                "pg_ctl",
                "-D",
                str(pgdata),
                "-l",
                str(self.data / "logs" / "postgres.log"),
                "-o",
                f"-h 127.0.0.1 -p {settings['db_port']} -c shared_buffers=128MB",
                "-w",
                "start",
            )
        self.pg_started = True
        import psycopg
        from psycopg.conninfo import make_conninfo

        conninfo = make_conninfo(
            host="127.0.0.1",
            port=settings["db_port"],
            user="hylogger",
            password=settings["password"],
            dbname="postgres",
        )
        with psycopg.connect(conninfo, autocommit=True) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname='hylogger'").fetchone():
                conn.execute("CREATE DATABASE hylogger")
        os.environ["DATABASE_URL"] = make_conninfo(conninfo, dbname="hylogger")
        os.environ["HYLOGGER_DATA_DIR"] = str(self.data)
        os.environ["HYLOGGER_DESKTOP"] = "1"
        os.environ["OMP_NUM_THREADS"] = "4"
        os.environ["OPENBLAS_NUM_THREADS"] = "4"
        status("Preparing Explorer…")
        sys.path.insert(0, str(root() / "platform"))
        from db.migrate import migrate

        migrate(os.environ["DATABASE_URL"])
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            # This private database has no other worker while the desktop lock is held.
            conn.execute(
                "UPDATE job SET status=CASE WHEN status='cancel_requested' THEN 'cancelled' "
                "ELSE 'queued' END,worker=NULL WHERE status IN ('running','cancel_requested')"
            )
            conn.execute("DELETE FROM worker")
        self.port = free_port(self.port)
        api = self.spawn(
            "uvicorn",
            "--app-dir",
            str(root() / "platform"),
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        )
        self.spawn("hylogger.jobs.worker", "--lanes", "processed:1,raw:1,analysis:1")
        url = f"http://127.0.0.1:{self.port}"
        for _ in range(120):
            if api.poll() is not None:
                raise RuntimeError(f"Explorer failed to start. See {self.data / 'logs'}.")
            try:
                with urllib.request.urlopen(url + "/api/v2/health", timeout=2) as response:
                    if json.load(response)["status"] == "ok":
                        break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Explorer did not become ready; see the logs folder.")
        atomic_json(self.data / "desktop-running.json", {"url": url, "pid": os.getpid()})
        status("Explorer is ready. Downloads continue while this window is open.")
        return url

    def stop(self):
        import psutil

        for process in reversed(self.children):
            if process.poll() is not None:
                continue
            try:
                parent = psutil.Process(process.pid)
                tree = parent.children(recursive=True) + [parent]
                for child in reversed(tree):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(tree, timeout=8)
                for child in alive:
                    child.kill()
            except psutil.NoSuchProcess:
                pass
        if self.pg_started:
            self.pg_run(
                "pg_ctl", "-D", str(self.data / "postgres"), "-m", "fast", "-w", "stop", check=False
            )
        for handle in self.handles:
            handle.close()
        if self.lock:
            self.lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HyLoggerExplorer",
    )
    parser.add_argument("--port", type=int, default=8130)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--shutdown-after", type=float, default=0)
    args = parser.parse_args()
    desktop = Desktop(args.data_dir, args.port)
    window = None
    try:
        if not args.headless:
            import tkinter as tk

            window = tk.Tk()
            window.title("HyLogger Explorer")
            window.geometry("580x230")
            label = tk.Label(window, text="Starting Explorer…", wraplength=540, padx=20, pady=20)
            label.pack()

            def status(message):
                label.config(text=message)
                window.update()
        else:
            status = print
        url = desktop.start(status)
        if not args.no_browser:
            webbrowser.open(url + "/#/data")
        if window:
            tk.Button(
                window, text="Open Explorer", command=lambda: webbrowser.open(url + "/#/data")
            ).pack(pady=5)
            tk.Label(window, text=f"Data: {desktop.data}", wraplength=540).pack(pady=5)
            tk.Button(window, text="Close Explorer and save progress", command=window.destroy).pack(
                pady=5
            )
            if args.shutdown_after:
                window.after(int(args.shutdown_after * 1000), window.destroy)
            window.mainloop()
        else:
            started = time.monotonic()
            while not args.shutdown_after or time.monotonic() - started < args.shutdown_after:
                time.sleep(1)
    except Exception as error:
        import traceback

        desktop.data.mkdir(parents=True, exist_ok=True)
        (desktop.data / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        if window:
            from tkinter import messagebox

            messagebox.showerror("Explorer could not start", str(error))
        raise
    finally:
        desktop.stop()


if __name__ == "__main__":
    main()
