#!/usr/bin/env bash
set -e

# 从 gh keychain 读取 token，设为环境变量
export GITHUB_TOKEN_PM=$(gh auth token --user songsjun)
export GITHUB_TOKEN_OTTER=$(gh auth token --user otter9527)
export GITHUB_TOKEN_KAPY=$(gh auth token --user kapy9250)

echo "Tokens loaded:"
echo "  PM    (songsjun)  : ${GITHUB_TOKEN_PM:0:12}..."
echo "  Worker1 (otter9527): ${GITHUB_TOKEN_OTTER:0:12}..."
echo "  Worker2 (kapy9250) : ${GITHUB_TOKEN_KAPY:0:12}..."
echo ""
echo "Starting github-pm-agent for songsjun/StudyBuddy ..."
echo ""

github-pm-agent --config config.studybuddy.yaml daemon
