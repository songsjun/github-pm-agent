# StudyBuddy Issues Handover — 2026-03-24

## 目标
将 StudyBuddy 仓库（songsjun/StudyBuddy）的 issues #16-#24 全部处理完毕：
每个 issue 经历 implement → code_review → fix_iteration → pm_decision → merge 流程后关闭。

## 已完成
| Issue | PR | 状态 |
|-------|-----|------|
| #15   | #25 | ✅ merged |
| #19   | #26 | ✅ merged |
| #24   | #29 | ✅ merged |

## 当前状态（2026-03-24 ~17:00）

### Daemon 状态
- 进程：PID 49245，运行中（`ps aux | grep github-pm-agent`）
- 配置文件：`config.studybuddy.yaml`
- 运行目录：`.runtime-studybuddy/`
- 日志：`/tmp/studybuddy-daemon.log`
- **关键问题**：`GITHUB_TOKEN_PM`、`GITHUB_TOKEN_OTTER`、`GITHUB_TOKEN_KAPY` 环境变量未设置
  → daemon 每次都报 "token_env not set, falling back to gh_user" 警告
  → 所有操作都用 sjunsong 账号，导致 PR 自我审批被 GitHub 阻止

### Open PRs 需要处理

| PR | Issue | Phase | 状态 |
|----|-------|-------|------|
| #31 | #17 | pm_decision | tests pass, MERGEABLE, 无 review，需要审批后合并 |
| #32 | #16 | fix_iteration | tests FAIL — TypeScript 错误（见下方） |
| #33 | #21 | 需更新 state | tests pass（PR #33 是 #27 的替代，state.json 还写的 pr_number: 27） |
| #27 | #21 | stale | 旧 PR，应关闭 |
| #28 | #23 | fix_iteration | tests FAIL — 依赖 #18 的 route（见下方） |

### Issues 无 PR 尚未处理

| Issue | 标题 | 状态 |
|-------|------|------|
| #18 | Add goal creation API route | phase: implement, 无 PR |
| #20 | Implement normalized analysis result validator | phase: implement, 无 PR |
| #22 | Add dashboard summary endpoint | phase: implement, 无 PR |

## 关键阻塞：GitHub Token 分离

### 问题根因
GitHub 分支保护规则：**最后一次 push 的人不能自我审批 PR**。
当前所有操作（code push + PR review）都用同一个 sjunsong token。

### 解决方案（代码已实现，token 未注入）

代码已修改（`_get_worker_github_token()` in `workflow_orchestrator.py`）：
- Worker（otter9527 / kapy9250）push 代码
- PM（sjunsong）审批并合并

**需要做的**：重启 daemon 时带上以下 token：
```bash
pkill -f "github-pm-agent.*daemon"
export GITHUB_TOKEN_OTTER=$(gh auth token --user otter9527)
export GITHUB_TOKEN_KAPY=$(gh auth token --user kapy9250)
export GITHUB_TOKEN_PM=$(gh auth token --user sjunsong)
nohup uv run github-pm-agent --config config.studybuddy.yaml daemon --interval 30 \
  > /tmp/studybuddy-daemon.log 2>&1 &
```

gh CLI 的 otter9527 和 kapy9250 账号已在 keyring 中登录，`gh auth token --user otter9527` 可直接获取 token。

## 各 Issue 详细说明

### Issue #16（PR #32）— fix_iteration，tests FAIL

**错误**：
```
src/lib/goals.ts(26,14): error TS2353: 'status' does not exist in type 'StudyGoalWhereInput'
src/lib/goals.ts(30,7): 'targetName' does not exist in type 'StudyGoalSelect'
```
AI 生成的 `goals.ts` 跟已 merge 的 Prisma schema 字段不匹配。

**处理方法**：
1. state.json 的 `fix_iteration` phase 表示还有重试机会
2. 向队列注入 fix_iteration 阶段事件，或等 daemon 自动重试

### Issue #17（PR #31）— pm_decision，tests pass，MERGEABLE

state.json: `phase: pm_decision`, `pr_number: 31`

**处理方法**：
向队列注入 pm_decision 事件：
```bash
python3 -c "
import json, time
event = {
  'event_id': 'issue_coding:manual:17:pm_decision_trigger',
  'event_type': 'issue_coding',
  'source': 'manual',
  'occurred_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
  'repo': 'songsjun/StudyBuddy',
  'actor': 'sjunsong',
  'url': 'https://github.com/songsjun/StudyBuddy/issues/17',
  'title': '',
  'body': '',
  'target_kind': 'issue',
  'target_number': 17,
  'metadata': {
    'advance_to_phase': 'pm_decision',
    'artifacts': {}
  }
}
with open('.runtime-studybuddy/queue_pending.jsonl', 'a') as f:
    f.write(json.dumps(event) + chr(10))
print('injected')
"
```

### Issue #21（PR #33 是正确 PR，state 显示 #27）

state.json 的 `pr_number` 字段还是 27（旧 PR），但实际应该是 33。

**处理方法**：
1. 先手动更新 state.json 的 pr_number 为 33
2. 关闭旧的 PR #27：`gh pr close 27 --repo songsjun/StudyBuddy`
3. 注入 pm_decision 事件（同上，target_number: 21）

### Issue #23（PR #28）— 依赖 #18 的 route

**错误**：`Cannot find module '../../src/app/api/goals/route'`
→ 需要 issue #18 的代码先 merge，才能测试通过。

**处理顺序**：先处理 #18，merge 后再处理 #23。

### Issues #18、#20、#22 — 需要触发 implement

这三个 issue 在 `implement` 阶段，queue_pending 为空，需要手动注入。

```bash
python3 << 'EOF'
import json, time

issues = [
  (18, 'Add goal creation API route'),
  (20, 'Implement normalized analysis result validator'),
  (22, 'Add dashboard summary endpoint'),
]

for num, title in issues:
    event = {
      'event_id': f'issue_coding:manual:{num}:trigger_v1',
      'event_type': 'issue_coding',
      'source': 'manual',
      'occurred_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
      'repo': 'songsjun/StudyBuddy',
      'actor': 'sjunsong',
      'url': f'https://github.com/songsjun/StudyBuddy/issues/{num}',
      'title': title,
      'body': '',
      'target_kind': 'issue',
      'target_number': num,
      'metadata': {'action': 'labeled', 'labels': ['ready-to-code']}
    }
    with open('.runtime-studybuddy/queue_pending.jsonl', 'a') as f:
        f.write(json.dumps(event) + '\n')
    print(f'Injected issue #{num}')
EOF
```

## 关键代码改动（已 commit）

### `src/github_pm_agent/workflow_orchestrator.py`
- 新增 `_get_worker_github_token(executors)` 方法
- `coding_session` 和 `fix_coding_session` 动作改用 worker token 推代码

### `src/github_pm_agent/coding_session.py`
- `push_branch()` 和 `fix_and_push()` 在 push 前先做 `git rebase origin/main`
- 防止因其他 issue 先合并导致冲突

### `prompts/coding/implement.md`
- 增加 SQLite 不支持 Json 字段的关键规则
- 增加保留现有 exports、Prisma models、async getActiveStudyGoal 的规则
- 增加不回归已有依赖的规则

## 推荐处理顺序

1. **立即**：重启 daemon 带上三个 GitHub token（最优先）
2. 注入 issues #18、#20、#22 的 implement 事件
3. 等 #18 merge 后，注入 #23 的 fix_iteration 重试事件
4. 手动更新 issue #21 state 的 pr_number 为 33，注入 pm_decision
5. 注入 issue #17 的 pm_decision 事件
6. 关闭旧 PR #27

## 文件路径参考

- state 文件：`.runtime-studybuddy/workflows/songsjun__StudyBuddy/{issue_number}/state.json`
- queue：`.runtime-studybuddy/queue_pending.jsonl`
- daemon 日志：`/tmp/studybuddy-daemon.log`
- 实现 prompt：`prompts/coding/implement.md`
- orchestrator：`src/github_pm_agent/workflow_orchestrator.py`
- coding session：`src/github_pm_agent/coding_session.py`
