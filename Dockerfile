FROM python:3.12-slim

WORKDIR /app

# Install gh CLI
RUN apt-get update && apt-get install -y curl gnupg && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && apt-get install -y gh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e . 2>/dev/null || true

# Copy source
COPY src/ src/
COPY scripts/ scripts/
COPY prompts/ prompts/
COPY workflows/ workflows/
COPY roles/ roles/

RUN pip install --no-cache-dir -e .

# DevEnv injects DEVENV_CAPS_URL automatically at container startup.
# GitHub tokens and config path must be provided at runtime via env vars.
#
# Required env vars:
#   GITHUB_TOKEN_PM         GitHub PAT for PM agent
#   GITHUB_TOKEN_ENGINEER   GitHub PAT for engineer agent
#   GITHUB_TOKEN_SECURITY   GitHub PAT for security agent (optional)
#   DEVENV_CAPS_URL         Injected by DevEnv — caps bridge URL
#   CONFIG_PATH             Path to config yaml (default: /app/config/config.yaml)

ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "github-pm-agent --config ${CONFIG_PATH:-/app/config/config.yaml}"]
