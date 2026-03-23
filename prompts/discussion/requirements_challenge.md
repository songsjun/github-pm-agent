The brainstorm is complete and the PM has synthesized a direction. Before writing the PRD, challenge the emerging requirements from an assigned perspective.

**Discussion:** $discussion_title

**Problem Definition:**
$artifact_problem_synthesis

**Brainstorm Summary:**
$artifact_brainstorm

$human_comment
$pending_comments

---

**SCOPE CALIBRATION — do this first, silently:**
The scope is whatever was established in the problem definition. Every output must be calibrated to that scope. A single-user tool needs no enterprise features.

---

**YOUR ASSIGNMENT — Slot $slot_number of $total_slots**

$slot_number == 1:
## Kano Classifier
Classify every distinct feature or capability mentioned across the brainstorm into one of three categories:

**Basic (must-have)**: Absence causes frustration. Presence is taken for granted.
**Performance (linear)**: More = better. Users notice and appreciate improvements.
**Delighter (surprise)**: Unexpected. Users love it but didn't know to ask for it.

Use this table format:
| Feature | Kano Category | Reason (1 sentence) |
|---|---|---|
| ... | Basic / Performance / Delighter | ... |

Then flag: which items are being treated as Delighters but are actually Basics? These are dangerous misclassifications.

$slot_number == 2:
## MoSCoW Arbiter
Assign every feature or capability mentioned across the brainstorm a priority:

- **Must**: Without this, the product cannot be used for its core job. (Max 3 items)
- **Should**: Important but workable without for a first version.
- **Could**: Nice to have. Low effort, moderate value.
- **Won't (this version)**: Explicitly excluded from the first version.

Rules:
- Must list is capped at 3 items. If you have more than 3, you are wrong — re-examine.
- Every feature must appear in exactly one category.
- Justify each Must with one sentence tied to the core job from Phase 0.

$slot_number == 3:
## Assumption Recorder
Based on the brainstorm output, identify all assumptions that are being made implicitly.

Use this format for each assumption:
- **Assumption**: [statement — what the team is treating as true]
- **Type**: User behavior / Technical feasibility / Market / Scope
- **If wrong**: [what breaks or becomes invalid]
- **Risk**: High / Medium / Low
- **Cheapest validation**: [smallest test that could confirm or deny this assumption]

List at least 5 assumptions. Prioritize high-risk ones first.

If your slot number is greater than 3, cycle: use slot (($slot_number - 1) % 3) + 1 above.

---

Output your assigned perspective only. No preamble. Tables are preferred for clarity.
