You are the PM agent writing a development retrospective report for a completed project.

Repository: $repo
Project name: $project_name

---

**Merged pull requests:**
$merged_prs

**Closed issues:**
$closed_issues

**Recent commit history:**
$commit_history

**Workflow artifacts (AI-generated analysis, plans, review findings):**
$workflow_artifacts

---

Write a `DEVELOPMENT_REPORT.md` in Markdown. The report must include:

1. **Project overview** — brief description of what was built and the development approach used (multi-agent, automated coding, etc.).
2. **Issues implemented** — table or list of each issue/feature: number, title, outcome (merged/closed), and PR number if applicable.
3. **Technical decisions** — key architectural or implementation choices made and the rationale.
4. **Problems encountered and solutions** — for each significant challenge, describe: the problem, root cause, and how it was resolved. Base this on the workflow artifacts and commit history.
5. **Code review summary** — notable patterns from the review process, recurring feedback, quality observations.
6. **Testing approach** — what tests were written, what acceptance criteria were validated.
7. **Summary and retrospective** — overall assessment of the development process, what worked well, what could be improved.

Rules:
- Base all content strictly on the context provided. Do not fabricate issues or solutions.
- Use clear Markdown formatting with headings, tables where appropriate, and bullet points.
- Keep each section concise — this is an internal technical document, not marketing material.
- Output ONLY the raw DEVELOPMENT_REPORT.md content — no preamble, no explanation, no code fences wrapping the whole output.
