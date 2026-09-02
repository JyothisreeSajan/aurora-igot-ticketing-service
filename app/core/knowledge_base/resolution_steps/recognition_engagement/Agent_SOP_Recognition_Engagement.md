# Agent SOP — Recognition & Engagement Issues Resolution
**Platform:** iGOT Karmayogi | **Agent Framework:** LangGraph
**Applies to:** L1 AI Agent — Karma Points, Weekly Claps, Learning Hours, and Leaderboard Use Cases

---

## Global Agent Principles
- **Tool-first, ask-last.** Fetch relevant status via API immediately — before asking the user anything.
- **Single-pass diagnosis.** Run all relevant API calls upfront and deliver one complete, informed response.
- Be empathetic, concise, and professional in every response.

---

## Tool Registry

| Tool | Signature | Purpose |
|------|-----------|---------|
| `get_completed_courses` | `(email)` | Fetch the user's 5 most recent completed courses — fallback list when no course is named in the ticket (SOP-RE1 Flow A STEP 0) |
| `get_completed_events` | `(email)` | Fetch the user's 5 most recent completed events, each pre-annotated with `hours_since_start` / `is_live_participation` — fallback list + timing check (SOP-RE1 Flow B STEP 0/1) |
| `get_karma_course_status` | `(email, course_id)` | Fetch a course's karma credit status (`completion_credited`, `rating_credited`, `completion_points`, `rating_points`, `acbp`, `has_assessment`, `monthly_rank`) from `/api/karmapoints/read` (SOP-RE1 Flow A STEP 1-3, Edge Case 1) |
| `get_karma_event_status` | `(email, event_id)` | Fetch whether karma points were credited for a specific event (SOP-RE1 Flow B STEP 1) |

---
---

# SOP-RE1: Karma Points Issue

Adapted from the original chatbot SOP for ticket resolution (single-pass, ask-once-then-close
instead of a multi-turn chat loop; "raise a ticket" maps to `escalate=true` + reason on the
already-open ticket — there is no separate ticket-creation mechanism in this codebase).

## Flow A — Course karma points not credited

**STEP 0 — Identify the course.** If the ticket message already names a course, use it
directly. Otherwise call `get_completed_courses`, share the list, and ask the user to
confirm (ask once — `needs_clarification=true`, stop; if the reply still doesn't name a
course, close unresolved). Match case-insensitively / partially against the results.

**STEP 1 — Credit status.** `get_karma_course_status(email, course_id)`.

| completion_credited | rating_credited | Action |
|---|---|---|
| true | true | Resolved — already credited. Close. |
| false | false | **escalate=true** immediately — no completion or rating credit at all. Skip STEP 2/3. |
| false | true | → STEP 2 |
| true | false | Resolved — treated the same as "both credited." No ticket. |

**STEP 2 — Training Plan check.** Read `acbp`.
- `true` → **escalate=true** immediately (Training Plan course, completion points missing).
- `false` → STEP 3.

**STEP 3 — Monthly cap check.** Read `monthly_rank` (already computed by the tool — counts
only non-Training-Plan course completions in the same calendar month).
- `>= 5` → Resolved — only the first 4 completed courses/month are eligible. Close.
- `<= 4` (or unavailable) → **escalate=true** — discrepancy.

## Flow B — Event karma points not credited

**STEP 0 — Identify the event.** Same ask-once pattern as Flow A STEP 0, via
`get_completed_events`.

**STEP 1 — Timing + credit check.** Read `is_live_participation` /
`hours_since_start` from the matched event (≤4 hours from the event's start counts as live
participation — covers both "during the event" and shortly after). If absent (start time
unknown), skip straight to the credited check.
- `is_live_participation = false` → Resolved — points are only credited for live
  participation. Close.
- `is_live_participation = true` (or unavailable) → `get_karma_event_status(email,
  event_id)`.
  - `credited = true` → Resolved — already credited. Close.
  - `credited = false` → **escalate=true** — live participation confirmed, points missing.

## Edge Case 1 — Incorrect karma points (5 vs 10 vs 15)

Course identification same as Flow A STEP 0. `get_karma_course_status(email, course_id)`.

Expected points: `acbp=true, has_assessment=true` → 15. `acbp=true, has_assessment=false`
→ 10. `acbp=false` → 5 (subject to the same monthly-cap check as Flow A STEP 3, via
`monthly_rank` from the same result).

- `completion_points` matches expected → Resolved — points are correct. Close.
- Mismatch, `acbp=true` → **escalate=true**.
- Mismatch, `acbp=false`, `monthly_rank <= 4` → **escalate=true**.
- Mismatch, `acbp=false`, `monthly_rank >= 5` → Resolved — first-4-per-month rule explained.

## Edge Case 2 — Leaderboard vs overall karma points mismatch

No tools. Resolved — informational close: Leaderboard/Top Karmayogi shows last month's
points only; Overall/Individual shows cumulative points since joining. Expected to differ.

## Edge Case 3 — Learner pathway points (+25)

**Not implemented.** The source SOP itself flags this as pending Product Team
clarification on whether the monthly limit applies within pathways, and no tool exists to
check a pathway-specific monthly limit. **escalate=true** — same treatment as CA/APAR
SOP-3's unimplemented branches (skipped rather than half-implemented), pending
clarification.

## Edge Case 4 — Course already completed, added later to a Training Plan

No tools. Resolved — informational close: guide the user to the course's TOC page →
"Claim Karma Point" button.

---

## SOP-RE1 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| Both completion + rating credited | ❌ |
| Neither credited at all | ✅ |
| Only completion credited, rating not | ❌ |
| Only rating credited, Training Plan course | ✅ |
| Only rating credited, not Training Plan, `monthly_rank<=4` | ✅ |
| Only rating credited, not Training Plan, `monthly_rank>=5` | ❌ |
| Course/event name not confirmed after one ask | ❌ (closed unresolved) |
| Event live (≤4h) and credited | ❌ |
| Event live (≤4h) and not credited | ✅ |
| Event not live (>4h) | ❌ |
| Edge Case 1 — points match expected | ❌ |
| Edge Case 1 — mismatch, Training Plan | ✅ |
| Edge Case 1 — mismatch, not Training Plan, `rank<=4` | ✅ |
| Edge Case 1 — mismatch, not Training Plan, `rank>=5` | ❌ |
| Edge Case 2 — leaderboard vs overall | ❌ |
| Edge Case 3 — learner pathway | ✅ (pending clarification) |
| Edge Case 4 — claim button | ❌ |

---
---

# SOP-RE2: Weekly Claps Issue

**Status:** Not yet defined — placeholder.

---
---

# SOP-RE3: Learning Hours Issue - eHRMS

**Status:** Not yet defined — placeholder.

---
---

# SOP-RE4: Learning Hours Issue - Shiksha Path

**Status:** Not yet defined — placeholder.

---
---

# SOP-RE5: Learning Hours Issue - SPARROW / APAR

**Status:** Not yet defined — placeholder.

---
---

# SOP-RE6: Leader Board Issue

**Status:** Not yet defined — placeholder.
