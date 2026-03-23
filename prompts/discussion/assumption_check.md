The technical design has been approved. Before breaking down into implementation tasks, validate the assumptions underlying the plan from an assigned perspective.

**Discussion:** $discussion_title

**Problem Definition:**
$artifact_problem_synthesis

**Product Requirements:**
$artifact_requirements

**Final Technical Design:**
$artifact_final_design

$user_supplements
$human_comment
$pending_comments

---

**SCOPE CALIBRATION — do this first, silently:**
The scope is fixed by the approved PRD. Do not expand or contract it. Flag scope creep if you see it in the design.

---

**YOUR ASSIGNMENT — Slot $slot_number of $total_slots**

$slot_number == 1:
## Assumption Validator
Cross-reference the assumptions listed in the PRD against the approved technical design.

For each **high-risk assumption** from the PRD's "Key assumptions" section:
1. Is this assumption still present in the technical design? (Yes / Partially / No — explain)
2. Does the design address or mitigate it? (Yes / No / Unknown — explain)
3. If unmitigated: what is the cheapest way to validate this before writing code?

Then identify any **new assumptions** introduced by the technical design that were not in the PRD. List them with risk level.

Format as a table where possible.

$slot_number == 2:
## MVP Boundary Definer
Based on the MoSCoW priorities in the PRD and the approved technical design, define the true MVP.

**Rules:**
- MVP = the smallest version that validates the core assumptions and delivers the Must-have user jobs
- MVP is NOT a stripped-down full product — it is a focused test
- Every issue in the breakdown that follows must be classifiable as: MVP / Post-MVP / Nice-to-have

Output:
1. **MVP scope in one sentence**: What can a user do with the MVP and nothing else?
2. **MVP boundary**: List each Must-have feature — is it in MVP or deferred? Why?
3. **What to defer**: Which Should/Could items should wait until MVP assumptions are validated?
4. **First assumption to validate**: If you could only ship one thing first, what would maximally reduce uncertainty?

If your slot number is greater than 2, cycle: use slot (($slot_number - 1) % 2) + 1 above.

---

Output your assigned perspective only. Be specific — reference the actual assumptions and features from the documents above.
