# Multi-Agent Workflow 改造计划

## 原则
- 每步改动最小化，验证成功再继续
- 保持现有 engineer/security 配置向后兼容
- 程序/事件控制流程，提示词只负责思维内容

---

## Step 1: Worker 序号 + Slot 分发机制 【基础】
> 解耦 worker 数量与 workflow，PM 只定义 slots，worker 自认领

### 改动文件
- [ ] `roles/worker/system.md` — 新建 worker 通用系统提示词
- [ ] `roles/worker/permissions.json` — 新建 worker 权限
- [ ] `src/github_pm_agent/workflow_orchestrator.py` — 支持 `slots` 字段分发
- [ ] `config.example.yaml` — 添加 worker_index 示例

### 验证标准
- config 有 2 个 worker (index 1, 2)，step 有 slots=4
- worker 1 → slot 1, worker 2 → slot 2（共4个 slot，2 个 worker 各认领2个，顺序执行）
- 现有 engineer/security 配置不受影响

---

## Step 2: Phase 0 — 问题溯源 【最高价值】
> 在讨论功能之前先确认"要解决的问题是对的"

### 改动文件
- [ ] `prompts/discussion/problem_framing.md` — 新建（4槽：JTBD/5Whys/UserProxy/Challenger）
- [ ] `prompts/discussion/problem_synthesis.md` — 新建（PM 收敛问题定义）
- [ ] `workflows/discussion.yaml` — 在 brainstorm_perspectives 前插入 2 个新 phase

### 验证标准
- 新建 Discussion → Phase 0 先于 brainstorm 执行
- 4 个思维视角分别输出到 Discussion
- PM 输出包含"真正的问题"和"核心假设列表"
- Gate 触发，等待 owner 确认

---

## Step 3: Phase 1 重构 — 六顶思考帽 【增强发散质量】
> 替换 brainstorm_perspectives，引入结构化思维模式

### 改动文件
- [ ] `prompts/discussion/opportunity_explore.md` — 新建（替换 brainstorm_perspectives）
- [ ] `prompts/discussion/brainstorm.md` — 更新引用新 artifact 名称
- [ ] `workflows/discussion.yaml` — brainstorm_perspectives → opportunity_explore，改为 slots

### 验证标准
- 6 个帽子各自输出（worker 少时按 mod 复用）
- PM brainstorm 能看到6个视角内容

---

## Step 4: Phase 2 新增 — 需求挑战 【引入 Kano + MoSCoW】
> 在 PM 写 PRD 之前做一轮结构化需求审查

### 改动文件
- [ ] `prompts/discussion/requirements_challenge.md` — 新建（3槽：Kano/MoSCoW/假设记录）
- [ ] `prompts/discussion/requirements.md` — 更新模板加入 Kano 表、MoSCoW、假设清单章节
- [ ] `workflows/discussion.yaml` — requirements_perspectives → requirements_challenge，在 requirements 前插入

### 验证标准
- requirements_challenge 输出包含 Kano 分类和 MoSCoW 层次
- PRD 包含假设清单章节

---

## Step 5: Gate 增强 — 支持 CONFIRM_REVISE 【解锁补充需求】
> owner 确认时可附带修改要求，触发当前 PM 步骤重跑

### 改动文件
- [ ] `src/github_pm_agent/workflow_instance.py` — 新增 user_supplements 存取
- [ ] `src/github_pm_agent/phase_gate_scanner.py` — 响应分类（CONFIRM/CONFIRM_REVISE/REJECT/UNCLEAR）
- [ ] `src/github_pm_agent/workflow_orchestrator.py` — 注入 $user_supplements 变量

### 验证标准
- owner 回复"确认" → 正常前进
- owner 回复"确认，另外加上X" → 当前 PM 合成步骤重跑，X 出现在 PRD 中
- owner 回复"不对，重新来" → 当前 PM 步骤重跑
- 多次补充内容累积，不丢失

---

## Step 6: Phase 4 新增 — 假设复核 【完善验证闭环】
> 技术方案定稿后，回头验证假设，划定真正的 MVP 边界

### 改动文件
- [ ] `prompts/discussion/assumption_check.md` — 新建（2槽：假设验证/MVP边界）
- [ ] `workflows/discussion.yaml` — 在 tech_review gate 后、issue_breakdown 前插入

### 验证标准
- tech_review gate 通过后自动触发 assumption_check
- 输出包含高风险假设列表和 MVP 建议
- 然后才进入 issue_breakdown

---

## Step 7: 澄清机制 — Clarification 【处理模糊输入】
> worker 识别出信息不足时，系统主动向 owner 提问，带答案重跑

### 改动文件
- [ ] `src/github_pm_agent/workflow_instance.py` — suspend/resume 状态
- [ ] `src/github_pm_agent/workflow_orchestrator.py` — 检测 blocking_unknowns，发问，暂停
- [ ] `src/github_pm_agent/phase_gate_scanner.py` — 识别 clarification 回复，触发 resume
- [ ] `prompts/discussion/problem_framing.md` — 输出格式加 blocking_unknowns 字段

### 验证标准
- 发布极简模糊需求（3个字）→ 触发澄清提问
- owner 回答后 → 带答案重跑 problem_framing
- 正常需求不触发澄清

---

---

## Step 8: Back Half — Issue Analysis Workflow 【实施前分析】
> 新 issue 开启时，workers 从3个角度分析，各自评论

### 改动文件
- [x] `src/github_pm_agent/workflow_orchestrator.py` — 添加 `_post_output_comment` 通用评论方法，支持 issue/PR 事件；添加 `trigger_action` 过滤；变量别名 issue_title/issue_body/pr_title/pr_body
- [x] `workflows/issue_changed.yaml` — 改为 steps 模式，trigger_action: opened，slots=3
- [x] `prompts/issue/worker_analysis.md` — 3槽：实施规划/风险评估/代码定位

### 验证标准
- issue opened → 3个 worker 各自评论分析
- issue edited/labeled → 跳过（不触发）
- 同一 issue 第二次触发 → 跳过（已完成）
- 无讨论评论被误触发

---

## 进度

| Step | 状态 | 备注 |
|------|------|------|
| 1    | ✅ 完成 | 125 tests pass |
| 2    | ✅ 完成 | 125 tests pass |
| 3    | ✅ 完成 | 125 tests pass |
| 4    | ✅ 完成 | 125 tests pass |
| 5    | ✅ 完成 | 125 tests pass |
| 6    | ✅ 完成 | 125 tests pass |
| 7    | ✅ 完成 | 125 tests pass |
| 8    | ✅ 完成 | 130 tests pass |
| R1   | ✅ 完成 | Refactor: 4 design flaws fixed, 137 tests pass |
| C1   | ✅ 完成 | DevEnvClient REST API 封装，19 tests pass |
| C2   | ✅ 完成 | CodingSession 生命周期管理，13 tests pass |
| C3   | ✅ 完成 | issue_coding.yaml + 4 prompts |
| C4   | ✅ 完成 | Actions: coding_session / run_tests / merge_or_reopen |
| C5   | ✅ 完成 | 编排器 3 new action dispatch blocks, 169 tests pass |
| C6   | ⬜ 待开始 | 端到端集成测试（需 devenv 真实环境）|

---

## 后半段：编码与测试 Agent 能力

### 流程设计
```
issue labeled "ready-to-code"
    ↓ workflows/issue_coding.yaml
    ↓
Phase: implement     (slots=1, worker)
  action: coding_session
  - 创建 devenv workspace: issue-{safe_repo}-{number}
  - 容器内 git clone + 依赖安装
  - codex cap 实现代码（最多3轮：写→测→修）
  - 推送 branch + 创建 PR
    ↓
Phase: code_review   (slots=2, workers)
  - Worker slot1: 正确性 & 边界条件
  - Worker slot2: 设计 & 可维护性
  - 各自评论 PR
    ↓
Phase: test_verify   (slots=1, worker)
  action: run_tests
  - devenv exec 运行测试套件
  - 解析结果，评论 PR
    ↓
Phase: pm_decision   (roles: [pm], gate: true)
  action: merge_or_reopen
  - 读取 review + 测试结果
  - 合并 PR 或 reopen issue
```

### Step C1: DevEnvClient
> REST API 封装：workspace / build / run / exec / logs / cp / delete

- [ ] `src/github_pm_agent/devenv_client.py` — 纯 HTTP 客户端
- [ ] `tests/test_devenv_client.py` — 单元测试（mock HTTP）

验证：所有 API 方法覆盖，超时/错误处理正确

---

### Step C2: CodingSession
> 管理编码工作区全生命周期；包含 codex 调用循环

- [ ] `src/github_pm_agent/coding_session.py`
  - `setup(repo, branch, issue_number)` — 创建 workspace + 启动容器
  - `run_codex(prompt)` → codex via `DEVENV_CAPS_URL/codex`
  - `run_tests(cmd)` → `devenv exec job_id <cmd>`
  - `commit_push(branch, message)` — git op inside container
  - `get_diff()` — 返回 unified diff
  - `teardown()` — 可选清理
- [ ] `tests/test_coding_session.py`

验证：3轮失败后抛出异常并清理 workspace

---

### Step C3: 工作流 + 提示词
> 新 workflow + 4 个提示词文件

- [ ] `workflows/issue_coding.yaml`
- [ ] `prompts/coding/implement.md` — 输出 JSON 编码任务描述
- [ ] `prompts/coding/code_review.md` — slot1: 正确性, slot2: 设计
- [ ] `prompts/coding/test_verify.md` — 测试结果解读 + 评论
- [ ] `prompts/coding/pm_decision.md` — merge or reopen 决策

---

### Step C4: Actions 扩展
> 在 actions.py + orchestrator 中支持新 action 类型

- [ ] `actions.py` — `coding_session()`, `run_tests()`, `merge_or_reopen()`
- [ ] `workflow_orchestrator.py` — dispatch `coding_session`, `run_tests`, `merge_or_reopen`
- [ ] `config.example.yaml` — 添加 `devenv.server_url` 配置

---

### Step C5: 编排器支持
> 与现有事件流集成，支持 issue_coding 工作流

- [ ] `app.py` — 注册 issue_coding.yaml
- [ ] `workflow_instance.py` — 添加 `coding` 状态字段存取
- [ ] `roles/engineer/permissions.json` — 添加 coding 相关权限

---

### Step C6: 集成测试
> 端到端验证（devenv mock 模式）

- [ ] `tests/test_coding_workflow.py` — 完整 issue→PR 流程
- [ ] `DEVENV_SERVER=mock://` 支持或 monkeypatch DevEnvClient
