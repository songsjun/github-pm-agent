#!/usr/bin/env python3
from __future__ import annotations

import argparse
import cgi
import json
import logging
import os
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

LOG = logging.getLogger("local_devenv_server")


class LocalState:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.contexts_dir = root / "contexts"
        self.jobs_dir = root / "jobs"
        self.workspaces_dir = root / "workspaces"
        self.tmp_dir = root / "tmp"
        for path in (self.contexts_dir, self.jobs_dir, self.workspaces_dir, self.tmp_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.docker_command = resolve_docker_command()

    def create_workspace(self, name: str) -> None:
        path = self.workspaces_dir / name
        if path.exists():
            raise FileExistsError(name)
        path.mkdir(parents=True, exist_ok=False)

    def list_workspaces(self) -> list[dict[str, Any]]:
        return [{"name": path.name} for path in sorted(self.workspaces_dir.iterdir()) if path.is_dir()]

    def delete_workspace(self, name: str) -> None:
        path = self.workspaces_dir / name
        if path.exists():
            shutil.rmtree(path)

    def save_context(self, filename: str, payload: bytes) -> str:
        upload_id = f"ctx-{uuid.uuid4().hex}"
        target = self.contexts_dir / f"{upload_id}-{Path(filename).name}"
        target.write_bytes(payload)
        return upload_id

    def context_path(self, upload_id: str) -> Path:
        matches = sorted(self.contexts_dir.glob(f"{upload_id}-*"))
        if not matches:
            raise FileNotFoundError(upload_id)
        return matches[0]

    def delete_context(self, upload_id: str) -> None:
        try:
            self.context_path(upload_id).unlink()
        except FileNotFoundError:
            return

    def create_build_job(
        self,
        upload_id: str,
        tag: str,
        workspace: str,
        build_args: dict[str, Any] | None,
    ) -> str:
        job_id = f"job-{uuid.uuid4().hex}"
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        log_path = job_dir / "build.log"
        record = {
            "id": job_id,
            "job_id": job_id,
            "kind": "build",
            "workspace": workspace,
            "status": "queued",
            "tag": tag,
            "upload_id": upload_id,
            "build_args": dict(build_args or {}),
            "log_path": str(log_path),
            "created_at": _utc_now(),
            "started_at": "",
            "completed_at": "",
            "exit_code": None,
            "error": "",
            "_process": None,
        }
        with self.lock:
            self.jobs[job_id] = record
        thread = threading.Thread(
            target=self._run_build_job,
            args=(job_id,),
            name=f"build-{job_id}",
            daemon=True,
        )
        thread.start()
        return job_id

    def list_jobs(self, workspace: str = "", status: str = "") -> list[dict[str, Any]]:
        with self.lock:
            records = [self._public_job(record) for record in self.jobs.values()]
        if workspace:
            records = [record for record in records if record.get("workspace") == workspace]
        if status:
            records = [record for record in records if record.get("status") == status]
        return sorted(records, key=lambda item: str(item.get("created_at", "")))

    def inspect_job(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            record = self.jobs.get(job_id)
            if record is None:
                raise FileNotFoundError(job_id)
            return self._public_job(record)

    def stop_job(self, job_id: str) -> None:
        with self.lock:
            record = self.jobs.get(job_id)
            if record is None:
                raise FileNotFoundError(job_id)
            process: subprocess.Popen[str] | None = record.get("_process")
            status = str(record.get("status") or "")
        if process is None or status in {"done", "failed", "cancelled", "evicted"}:
            return
        try:
            process.terminate()
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            with self.lock:
                record = self.jobs.get(job_id)
                if record is not None:
                    record["status"] = "cancelled"
                    record["completed_at"] = _utc_now()
                    record["exit_code"] = -signal.SIGTERM
                    record["error"] = "job cancelled"
                    record["_process"] = None

    def job_logs(self, job_id: str) -> str:
        with self.lock:
            record = self.jobs.get(job_id)
            if record is None:
                raise FileNotFoundError(job_id)
            log_path = Path(str(record["log_path"]))
        if not log_path.exists():
            return ""
        return log_path.read_text(encoding="utf-8", errors="replace")

    def _run_build_job(self, job_id: str) -> None:
        with self.lock:
            record = self.jobs.get(job_id)
            if record is None:
                return
            record["status"] = "building"
            record["started_at"] = _utc_now()
            log_path = Path(str(record["log_path"]))
            tag = str(record["tag"])
            upload_id = str(record["upload_id"])
            build_args = dict(record["build_args"])

        log_path.parent.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix=f"{job_id}-", dir=self.tmp_dir))
        exit_code = 1
        error_text = ""
        try:
            archive_path = self.context_path(upload_id)
            with tarfile.open(archive_path, "r:gz") as archive:
                archive.extractall(temp_root)
            command = [*self.docker_command, "build", "-t", tag]
            for key, value in build_args.items():
                command.extend(["--build-arg", f"{key}={value}"])
            command.append(str(temp_root))
            LOG.info("Starting build job %s: %s", job_id, " ".join(command))
            with log_path.open("w", encoding="utf-8") as handle:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                with self.lock:
                    record = self.jobs.get(job_id)
                    if record is not None:
                        record["_process"] = process
                        record["status"] = "building"
                assert process.stdout is not None
                for line in process.stdout:
                    handle.write(line)
                    handle.flush()
                exit_code = process.wait()
            if exit_code == 0:
                status = "done"
            else:
                status = "failed"
                error_text = f"docker build exited with status {exit_code}"
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            error_text = str(exc)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n[local-devenv-server] build failed: {exc}\n")
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
            with self.lock:
                record = self.jobs.get(job_id)
                if record is not None:
                    if record.get("status") not in {"cancelled"}:
                        record["status"] = status
                    record["completed_at"] = _utc_now()
                    record["exit_code"] = exit_code
                    record["error"] = error_text
                    record["_process"] = None

    @staticmethod
    def _public_job(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in record.items()
            if not key.startswith("_")
        }


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "LocalDevEnv/0.1"

    @property
    def state(self) -> LocalState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if parsed.path == "/api/workspaces":
            self._json(HTTPStatus.OK, self.state.list_workspaces())
            return
        if parsed.path == "/api/jobs":
            workspace = _query_value(query, "workspace")
            status = _query_value(query, "status")
            self._json(HTTPStatus.OK, self.state.list_jobs(workspace=workspace, status=status))
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            try:
                payload = self.state.inspect_job(job_id)
            except FileNotFoundError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            self._json(HTTPStatus.OK, payload)
            return
        if parsed.path.startswith("/api/logs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            try:
                payload = self.state.job_logs(job_id)
            except FileNotFoundError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            self._text(HTTPStatus.OK, payload)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": f"unknown path: {parsed.path}"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/workspaces":
            body = self._json_body()
            name = str((body or {}).get("name") or "").strip()
            if not name:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "missing workspace name"})
                return
            try:
                self.state.create_workspace(name)
            except FileExistsError:
                self._json(HTTPStatus.CONFLICT, {"error": "workspace already exists"})
                return
            self._json(HTTPStatus.CREATED, {"name": name})
            return
        if parsed.path == "/api/files/context":
            try:
                filename, payload = self._multipart_file("file")
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            upload_id = self.state.save_context(filename, payload)
            self._json(HTTPStatus.CREATED, {"upload_id": upload_id})
            return
        if parsed.path == "/api/jobs/build":
            body = self._json_body()
            upload_id = str((body or {}).get("upload_id") or "").strip()
            tag = str((body or {}).get("tag") or "").strip()
            workspace = str((body or {}).get("workspace") or "").strip()
            if not upload_id or not tag:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "upload_id and tag are required"})
                return
            try:
                self.state.context_path(upload_id)
            except FileNotFoundError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "context not found"})
                return
            job_id = self.state.create_build_job(
                upload_id=upload_id,
                tag=tag,
                workspace=workspace,
                build_args=(body or {}).get("build_args"),
            )
            self._json(HTTPStatus.CREATED, {"id": job_id})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": f"unknown path: {parsed.path}"})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/workspaces/"):
            name = parsed.path.rsplit("/", 1)[-1]
            self.state.delete_workspace(name)
            self._empty(HTTPStatus.NO_CONTENT)
            return
        if parsed.path.startswith("/api/files/context/"):
            context_id = parsed.path.rsplit("/", 1)[-1]
            self.state.delete_context(context_id)
            self._empty(HTTPStatus.NO_CONTENT)
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            try:
                self.state.stop_job(job_id)
            except FileNotFoundError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            self._empty(HTTPStatus.NO_CONTENT)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": f"unknown path: {parsed.path}"})

    def log_message(self, format: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), format % args)

    def _json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def _multipart_file(self, field_name: str) -> tuple[str, bytes]:
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
            keep_blank_values=True,
        )
        if field_name not in form:
            raise ValueError(f"missing multipart field {field_name!r}")
        field = form[field_name]
        filename = getattr(field, "filename", "") or "context.tar.gz"
        payload = field.file.read() if field.file else b""
        return filename, payload

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _text(self, status: HTTPStatus, payload: str) -> None:
        raw = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.end_headers()


def _query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or [""]
    return values[0]


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def resolve_docker_command() -> list[str]:
    for command in (["docker"], ["sudo", "-n", "docker"]):
        probe = subprocess.run(
            [*command, "ps"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            LOG.info("Using Docker command prefix: %s", " ".join(command))
            return command
    LOG.warning("Falling back to plain docker command even though daemon probe failed")
    return ["docker"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a minimal local DevEnv-compatible server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17070)
    parser.add_argument("--state-dir", default=".runtime/local-devenv")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    state = LocalState(Path(args.state_dir).resolve())
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    server.state = state  # type: ignore[attr-defined]
    LOG.info("Local DevEnv server listening on http://%s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Shutting down local DevEnv server")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
