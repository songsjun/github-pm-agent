"""REST client for the DevEnv service."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from typing import Any, Literal

logger = logging.getLogger(__name__)

DEFAULT_SERVER_URL = "http://localhost:7070"
TERMINAL_JOB_STATUSES = {"done", "failed", "cancelled", "evicted"}


class DevEnvError(RuntimeError):
    """Raised when the DevEnv API cannot be reached or returns an error."""


class DevEnvClient:
    """Thin DevEnv REST API client built on top of ``urllib.request``."""

    def __init__(self, server_url: str = "", timeout: int = 60) -> None:
        """
        Initialize a DevEnv client.

        When ``server_url`` is omitted, the client resolves it from
        ``DEVENV_SERVER`` and falls back to ``http://localhost:7070``.
        """

        resolved_server_url = server_url or os.environ.get("DEVENV_SERVER") or DEFAULT_SERVER_URL
        self.server_url = resolved_server_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> bool:
        """Return ``True`` when the DevEnv server reports a healthy status."""

        payload = self._expect_dict(self._request("GET", "/health"), "health")
        return payload.get("status") == "ok"

    def create_workspace(self, name: str) -> dict[str, Any]:
        """Create a workspace and return the server payload."""

        return self._expect_dict(
            self._request("POST", "/api/workspaces", json_body={"name": name}),
            "create_workspace",
        )

    def list_workspaces(self) -> list[dict[str, Any]]:
        """Return all workspaces known to the server."""

        return self._expect_list(self._request("GET", "/api/workspaces"), "list_workspaces")

    def delete_workspace(self, name: str) -> None:
        """Delete a workspace by name."""

        self._request(
            "DELETE",
            f"/api/workspaces/{self._quote_path(name)}",
            response_type="none",
        )

    def upload_context(self, data: bytes, filename: str = "context.tar.gz") -> str:
        """Upload a build context archive and return the resulting context id."""

        body, content_type = self._build_multipart_form_data(
            field_name="file",
            filename=filename,
            data=data,
            mime_type="application/gzip",
        )
        payload = self._expect_dict(
            self._request(
                "POST",
                "/api/files/context",
                data=body,
                headers={"Content-Type": content_type},
            ),
            "upload_context",
        )
        context_id = str(payload.get("upload_id") or payload.get("id", "")).strip()
        if not context_id:
            raise DevEnvError("upload_context response missing id")
        return context_id

    def delete_context(self, context_id: str) -> None:
        """Delete a previously uploaded build context."""

        self._request(
            "DELETE",
            f"/api/files/context/{self._quote_path(context_id)}",
            response_type="none",
        )

    def build_image(
        self,
        context_id: str,
        tag: str,
        workspace: str = "",
        build_args: dict[str, Any] | None = None,
    ) -> str:
        """Start an image build job and return its job id."""

        payload = self._expect_dict(
            self._request(
                "POST",
                "/api/jobs/build",
                json_body={
                    "upload_id": context_id,
                    "tag": tag,
                    "workspace": workspace,
                    "build_args": build_args or {},
                },
            ),
            "build_image",
        )
        job_id = str(payload.get("id") or payload.get("job_id", "")).strip()
        if not job_id:
            raise DevEnvError("build_image response missing job_id")
        return job_id

    def run_container(
        self,
        image: str,
        workspace: str = "",
        cmd: str = "",
        env: dict[str, str] | None = None,
        ports: list[int] | None = None,
    ) -> str:
        """Start a container job and return its job id."""

        payload = self._expect_dict(
            self._request(
                "POST",
                "/api/jobs/run",
                json_body={
                    "image": image,
                    "workspace": workspace,
                    "cmd": cmd,
                    "env": env or {},
                    "ports": ports or [],
                },
            ),
            "run_container",
        )
        job_id = str(payload.get("id") or payload.get("job_id", "")).strip()
        if not job_id:
            raise DevEnvError("run_container response missing job_id")
        return job_id

    def exec_in_job(
        self,
        job_id: str,
        command: list[str],
        env: dict[str, str] | None = None,
        workdir: str = "",
    ) -> dict[str, Any]:
        """Execute a command inside a running job container."""

        return self._expect_dict(
            self._request(
                "POST",
                f"/api/jobs/{self._quote_path(job_id)}/exec",
                json_body={
                    "command": command,
                    "env": env or {},
                    "workdir": workdir,
                },
            ),
            "exec_in_job",
        )

    def get_artifact(self, job_id: str, path: str) -> bytes:
        """Fetch an artifact tarball for the given job path."""

        return self._request(
            "POST",
            f"/api/jobs/{self._quote_path(job_id)}/artifact",
            json_body={"path": path},
            response_type="bytes",
        )

    def list_jobs(self, workspace: str = "", status: str = "") -> list[dict[str, Any]]:
        """Return jobs filtered by workspace and/or status."""

        return self._expect_list(
            self._request(
                "GET",
                "/api/jobs",
                query={"workspace": workspace, "status": status},
            ),
            "list_jobs",
        )

    def inspect_job(self, job_id: str) -> dict[str, Any]:
        """Return the full job payload for a given job id."""

        return self._expect_dict(
            self._request("GET", f"/api/jobs/{self._quote_path(job_id)}"),
            "inspect_job",
        )

    def stop_job(self, job_id: str) -> None:
        """Stop and remove a job."""

        self._request("DELETE", f"/api/jobs/{self._quote_path(job_id)}", response_type="none")

    def get_logs(self, job_id: str, tail: int = 0) -> str:
        """Return plain-text logs for a job."""

        query: dict[str, Any] = {}
        if tail > 0:
            query["tail"] = tail
        return self._request(
            "GET",
            f"/api/logs/{self._quote_path(job_id)}",
            query=query,
            response_type="text",
        )

    def wait_for_job(
        self,
        job_id: str,
        poll_interval: float = 2.0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """
        Poll a job until it reaches a terminal state.

        Raises ``DevEnvError`` when the job fails, is evicted, or does not
        complete before ``timeout`` seconds.
        """

        deadline = time.monotonic() + timeout
        while True:
            job = self.inspect_job(job_id)
            status = str(job.get("status", "")).strip().lower()
            if status in TERMINAL_JOB_STATUSES:
                if status in {"failed", "evicted"}:
                    error_text = str(job.get("error", "")).strip()
                    suffix = f": {error_text}" if error_text else ""
                    raise DevEnvError(f"job {job_id} ended with status {status}{suffix}")
                return job
            if time.monotonic() >= deadline:
                raise DevEnvError(f"timed out waiting for job {job_id} after {timeout:.1f}s")
            time.sleep(poll_interval)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        response_type: Literal["json", "text", "bytes", "none"] = "json",
    ) -> Any:
        """Perform a single HTTP request and normalize failures as DevEnvError."""

        request_body = data
        request_headers = dict(headers or {})
        if json_body is not None:
            request_body = json.dumps(dict(json_body)).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        url = self._build_url(path, query)
        payload_summary = self._summarize_payload(json_body, request_body)
        logger.debug("DevEnv API %s %s%s", method, url, payload_summary)
        request = urllib.request.Request(url, data=request_body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read()
        except urllib.error.HTTPError as exc:
            detail = self._decode_error_body(exc)
            raise DevEnvError(f"{method} {url} failed with status {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise DevEnvError(f"{method} {url} failed: {exc.reason}") from exc
        except OSError as exc:
            raise DevEnvError(f"{method} {url} failed: {exc}") from exc
        return self._parse_response(raw_body, method=method, url=url, response_type=response_type)

    def _build_url(self, path: str, query: Mapping[str, Any] | None = None) -> str:
        """Return an absolute request URL for the given API path and query."""

        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.server_url}{normalized_path}"
        if not query:
            return url
        query_pairs = [
            (key, str(value))
            for key, value in query.items()
            if value not in (None, "")
        ]
        if not query_pairs:
            return url
        return f"{url}?{urllib.parse.urlencode(query_pairs)}"

    def _parse_response(
        self,
        raw_body: bytes,
        *,
        method: str,
        url: str,
        response_type: Literal["json", "text", "bytes", "none"],
    ) -> Any:
        """Decode a raw response body into the requested representation."""

        if response_type == "none":
            return None
        if response_type == "bytes":
            return raw_body
        text = raw_body.decode("utf-8", errors="replace") if raw_body else ""
        if response_type == "text":
            return text
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise DevEnvError(f"{method} {url} returned invalid JSON: {exc}") from exc

    def _build_multipart_form_data(
        self,
        *,
        field_name: str,
        filename: str,
        data: bytes,
        mime_type: str,
    ) -> tuple[bytes, str]:
        """Build a multipart/form-data request body for a single file upload."""

        boundary = f"devenv-client-{uuid.uuid4().hex}"
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {mime_type}\r\n\r\n".encode("ascii"),
                data,
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        return body, f"multipart/form-data; boundary={boundary}"

    def _decode_error_body(self, error: urllib.error.HTTPError) -> str:
        """Extract a useful message from an HTTP error response."""

        raw_body = error.read()
        if not raw_body:
            return error.reason or "empty response body"
        encoding = "utf-8"
        if error.headers is not None:
            encoding = error.headers.get_content_charset("utf-8")
        text = raw_body.decode(encoding, errors="replace").strip()
        if not text:
            return error.reason or "empty response body"
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(payload, dict):
            for key in ("error", "message", "detail"):
                value = payload.get(key)
                if value:
                    return str(value)
        return text

    def _summarize_payload(
        self,
        json_body: Mapping[str, Any] | None,
        request_body: bytes | None,
    ) -> str:
        """Return a compact payload summary for debug logging."""

        if json_body is not None:
            return f" json_keys={sorted(json_body)}"
        if request_body is not None:
            return f" bytes={len(request_body)}"
        return ""

    @staticmethod
    def _expect_dict(payload: Any, context: str) -> dict[str, Any]:
        """Validate that the decoded payload is a JSON object."""

        if not isinstance(payload, dict):
            raise DevEnvError(f"{context} returned unexpected payload type: {type(payload).__name__}")
        return payload

    @staticmethod
    def _expect_list(payload: Any, context: str) -> list[dict[str, Any]]:
        """Validate that the decoded payload is a JSON array of objects."""

        if not isinstance(payload, list):
            raise DevEnvError(f"{context} returned unexpected payload type: {type(payload).__name__}")
        if not all(isinstance(item, dict) for item in payload):
            raise DevEnvError(f"{context} returned a list with non-object items")
        return payload

    @staticmethod
    def _quote_path(value: str) -> str:
        """Quote a value for safe inclusion in a URL path segment."""

        return urllib.parse.quote(value, safe="")
