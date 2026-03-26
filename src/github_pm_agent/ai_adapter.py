from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict

from github_pm_agent.models import AiRequest, AiResponse
from github_pm_agent.prompt_library import PromptLibrary
from github_pm_agent.session_store import SessionStore


class AIAdapterManager:
    def __init__(
        self,
        project_root: Any,
        config: Dict[str, Any],
        prompt_library: PromptLibrary,
        session_store: SessionStore,
    ) -> None:
        self.project_root = project_root
        self.config = config
        self.prompt_library = prompt_library
        self.session_store = session_store

    def generate(self, request: AiRequest) -> AiResponse:
        rendered = self._render_request(request)
        provider_config = self._provider_config(request.provider)
        provider_type = provider_config.get("type", "shell")
        if provider_type == "shell":
            response = self._run_shell(provider_config, request, rendered)
        elif provider_type == "cli_script":
            response = self._run_cli_script(provider_config, request, rendered)
        elif provider_type == "openai_compatible":
            response = self._run_openai_compatible(provider_config, request, rendered)
        elif provider_type == "devenv_caps":
            response = self._run_devenv_caps(provider_config, request, rendered)
        else:
            raise RuntimeError(f"unsupported provider type: {provider_type}")
        if request.session_key:
            user_turn = json.dumps(request.variables, ensure_ascii=False)
            self.session_store.append_turn(request.session_key, user_turn, response.content)
        return response

    def _provider_config(self, provider_name: str) -> Dict[str, Any]:
        providers = self.config.get("ai", {}).get("providers", {})
        if provider_name not in providers:
            raise RuntimeError(f"provider not configured: {provider_name}")
        return providers[provider_name]

    def default_provider(self) -> str:
        return self.config.get("ai", {}).get("default_provider", "shell")

    def default_model(self, provider_name: str = "") -> str:
        provider_name = provider_name or self.default_provider()
        provider_config = self._provider_config(provider_name)
        return provider_config.get("default_model") or self.config.get("ai", {}).get("default_model", "gpt-5")

    def _render_request(self, request: AiRequest) -> str:
        transcript = ""
        if request.session_key:
            turns = self.session_store.recent_transcript(request.session_key)
            if turns:
                transcript = "\n\n".join(
                    f"TURN {idx + 1}\nREQUEST:\n{turn['request']}\n\nRESPONSE:\n{turn['response']}"
                    for idx, turn in enumerate(turns)
                )
        return self.prompt_library.render(
            system_prompt_path=request.system_prompt_path,
            prompt_path=request.prompt_path,
            variables=request.variables,
            output_template_path=request.output_template_path or "",
            file_refs=request.file_refs,
            memory_refs=request.memory_refs,
            skill_refs=request.skill_refs,
            artifact_refs=request.artifact_refs,
            transcript=transcript,
        )

    def _run_shell(self, provider_config: Dict[str, Any], request: AiRequest, rendered: str) -> AiResponse:
        command_template = provider_config.get("command")
        if not command_template:
            raise RuntimeError("shell provider requires a command")
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(rendered)
            input_file = handle.name
        placeholders = {
            "$input_file": input_file,
            "$model": request.model,
            "$provider": request.provider,
            "$session_id": request.session_key or "",
        }
        command = []
        for part in command_template:
            value = str(part)
            for needle, replacement in placeholders.items():
                value = value.replace(needle, replacement)
            command.append(value)
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        stdout = result.stdout.strip()
        try:
            raw = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError:
            raw = {"stdout": stdout}
        content = raw.get("output") if isinstance(raw, dict) else stdout
        if not content:
            content = stdout
        return AiResponse(
            provider=request.provider,
            model=request.model,
            content=content,
            raw=raw if isinstance(raw, dict) else {"raw": raw},
            session_key=request.session_key,
        )

    def _run_cli_script(self, provider_config: Dict[str, Any], request: AiRequest, rendered: str) -> AiResponse:
        script_path = Path(provider_config["script"])
        if not script_path.is_absolute():
            script_path = Path(self.project_root) / script_path
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(rendered)
            input_file = handle.name
        timeout_seconds = int(provider_config.get("timeout_seconds", 600))

        command = [
            provider_config.get("python_path", "python3"),
            str(script_path),
            "--provider",
            provider_config["provider_name"],
            "--model",
            request.model,
            "--input-file",
            input_file,
            "--cwd",
            str(request.cwd or self.project_root),
        ]
        if request.session_key:
            command.extend(["--session-key", request.session_key])
        if request.output_schema_path:
            schema_path = Path(request.output_schema_path)
            if not schema_path.is_absolute():
                schema_path = Path(self.project_root) / schema_path
            command.extend(["--schema-file", str(schema_path)])
        if provider_config.get("codex_path"):
            command.extend(["--codex-path", provider_config["codex_path"]])
        if provider_config.get("gemini_path"):
            command.extend(["--gemini-path", provider_config["gemini_path"]])
        if provider_config.get("reasoning_effort"):
            command.extend(["--reasoning-effort", provider_config["reasoning_effort"]])
        command.extend(["--timeout-seconds", str(timeout_seconds)])

        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=str(request.cwd or self.project_root),
            timeout=timeout_seconds + 30,
        )
        raw = json.loads(result.stdout.strip()) if result.stdout.strip() else {}
        return AiResponse(
            provider=request.provider,
            model=request.model,
            content=raw.get("output", ""),
            raw=raw,
            session_key=raw.get("session_key") or request.session_key,
        )

    def _run_devenv_caps(self, provider_config: Dict[str, Any], request: AiRequest, rendered: str) -> AiResponse:
        """Call a DevEnv Capability Bridge endpoint (http_proxy or exec type wrapping codex/ollama/etc.)."""
        caps_url_env = provider_config.get("caps_url_env", "DEVENV_CAPS_URL")
        caps_url = os.environ.get(caps_url_env, "").rstrip("/")
        if not caps_url:
            raise RuntimeError(
                f"DevEnv caps URL not set — set env var {caps_url_env} "
                f"(container should have DEVENV_CAPS_URL injected automatically)"
            )
        capability = provider_config.get("capability", "codex")
        url = f"{caps_url}/{capability}"

        # Pass model selection via ?args= if model_arg template is configured
        # e.g. model_arg: "-c model=$model"
        model_arg_template = provider_config.get("model_arg", "")
        if model_arg_template and request.model:
            args_value = model_arg_template.replace("$model", request.model)
            url = f"{url}?{urllib.parse.urlencode({'args': args_value})}"

        body = rendered.encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        timeout = provider_config.get("timeout", 300)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            exit_code = int(resp.headers.get("X-Exit-Code", "0"))

        if exit_code != 0:
            raise RuntimeError(
                f"devenv caps '{capability}' failed (exit {exit_code}): {content[:300]}"
            )

        return AiResponse(
            provider=request.provider,
            model=request.model,
            content=content.strip(),
            raw={"output": content.strip(), "exit_code": exit_code},
            session_key=request.session_key,
        )

    def _run_openai_compatible(self, provider_config: Dict[str, Any], request: AiRequest, rendered: str) -> AiResponse:
        api_key = os.environ.get(provider_config.get("api_key_env", "OPENAI_API_KEY"))
        if not api_key:
            raise RuntimeError("missing API key for openai-compatible provider")
        body = json.dumps(
            {
                "model": request.model,
                "messages": [
                    {"role": "user", "content": rendered},
                ],
                "temperature": 0.2,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            provider_config["base_url"],
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return AiResponse(
            provider=request.provider,
            model=request.model,
            content=content,
            raw=payload,
            session_key=request.session_key,
        )
