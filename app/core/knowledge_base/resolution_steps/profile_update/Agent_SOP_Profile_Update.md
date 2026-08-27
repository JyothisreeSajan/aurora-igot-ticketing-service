# Agent SOP — Profile Update Issues Resolution
**Platform:** iGOT Karmayogi | **Agent Framework:** LangGraph
**Applies to:** L1 AI Agent — Profile Verification, Designation/Group Update, and Leaderboard Use Cases

---

## Global Agent Principles
- **Tool-first, ask-last.** Fetch profile status, pending request status, and admin details via API immediately — before asking the user anything or describing what might be wrong.
- **Single-pass diagnosis.** Run all relevant API calls in parallel upfront and deliver one complete, informed response rather than a sequential back-and-forth.
- **Never commit to approval timelines.** Approvals depend on the Organization Admin. Do not give time estimates for designation or group verification.
- **Org Admin unavailable — Global Rule.** If no Org Admin is found for any scenario, always fall back to YP contact details. This rule applies across all steps in this category.

---

## Tool Registry

| Tool | Signature | Purpose |
|------|-----------|---------|
| `get_user_profile` | `(email)` | Fetch profile verification status, group, designation, department, organization, and designation request status (`profileDesignationStatus`) |
| `get_org_admin_details` | `(org_id)` | Fetch Org Admin / MDO Leader name and email for the user's organization |
| `get_yp_am_details` | `(ministry_or_state)` | Fetch YP/AM name, email, and contact details |

---
---

# SOP-P1: Profile Verification / Designation or Group Not Verified / Verified Community Badge Not Visible

## Purpose
Resolve cases where a user's profile verification is pending, their designation or group is not verified or not reflecting, or the Verified Community Badge (green tick) is not visible next to their name.

---

## STEP 1 — Fetch Profile and Request Status Immediately

**Type:** Run as soon as the issue is identified. No user prompt required.

**[TOOL CALL]**
```
get_user_profile(email = <user_email>)
  → returns: verification_status, group, designation, department, org_id, ministry_or_state, profileDesignationStatus
```

**Route based on combined results:**

| Profile Verification Status | Designation Request Status (`profileDesignationStatus`) | Action |
|-----------------------------|---------------------------------------------------------|--------|
| ✅ **Verified** | Any | → STEP 2 (Already verified — guide badge check) |
| ❌ **Not Verified** | `pending` | → STEP 3 (Request pending — check for admin) |
| ❌ **Not Verified** | `none` | → STEP 4 (No request — guide user to submit) |
| ❌ **Not Verified** | `approved` but not reflecting | → STEP 4 (Treat as no active request — guide re-submission) |

> **Agent note:** All scenarios the user may describe (designation not verified, designation not showing
> after submission, user claims they submitted but nothing is found) reduce to the same two-branch
> logic above. The user's description does not change the routing — the API state does.

---

## STEP 2 — Profile Already Verified → Guide Badge Check

**Ticket rule:** ❌ No ticket. Close after responding.

**User Message:**
> "Upon checking, we found that your profile has already been **successfully verified** and there are no pending verification requests.
>
> To locate your **Verified Community Badge**:
> 1. Go to your **Profile**.
> 2. Look at the area **next to your name**.
> 3. A **green tick mark** should be visible next to your name.
>
> The green tick indicates that the Verified Community Badge has been successfully assigned to your profile.
>
> Please let us know if you are still unable to see the badge after checking."

---

## STEP 3 — Designation Request Pending → Check Org Admin Availability

**[TOOL CALL]**
```
get_org_admin_details(org_id = <user_profile.org_id>)
```

**Route based on result:**

| Org Admin Available? | Action |
|---------------------|--------|
| ✅ Admin found | → STEP 3A |
| ❌ No admin found | → STEP 3B |

---

## STEP 3A — Pending Request, Org Admin Available → Share Admin Contact

**Ticket rule:** ❌ No ticket. Close after responding.

**User Message:**
> "Upon checking, we found that your **designation/group update request has already been submitted** and is currently awaiting approval from your Organization Admin.
>
> Kindly contact the admin below and request them to review and approve your request:
>
> **Admin Name:** [org_admin_name]
> **Admin Email ID:** [org_admin_email]
>
> Your designation and group details will reflect in your profile once the approval is completed."

---

## STEP 3B — Pending Request, No Org Admin Available → Share YP Contact

**[TOOL CALL]**
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

**Ticket rule:** ❌ No ticket. Close after responding.

**User Message:**
> "Upon checking, we found that your **designation/group update request has already been submitted**. However, there is currently no Organization Admin available to approve the request.
>
> Kindly connect with the concerned YP for further assistance:
>
> **YP Name:** [yp_name]
> **YP Email ID:** [yp_email]"

---

## STEP 4 — No Pending Request Found → Guide User to Submit

**Ticket rule:** ❌ No ticket. Close after responding.

**User Message:**
> "Upon checking, we found that there is currently no active designation/group update request under your profile.
>
> Kindly follow the steps below to submit a request:
>
> 1. Click on **View Profile**.
> 2. Navigate to **Primary Details** and click the **Edit (Pen) Icon**.
> 3. Update the correct **Group** and **Designation**.
> 4. Click **Send for Approval**.
>
> Once submitted, your request will be sent to the Organization Admin for approval. Your designation and group will reflect in your profile after approval."

> **Agent note:** If the user claims they already submitted a request but the system shows no pending
> entry, still deliver this same guidance — the request is not in the system regardless of the user's
> recollection, so the correct action is to re-submit. Do not open a clarification loop.

---

## STEP 4A — After User Re-submits (Follow-up)

If the user confirms they have re-submitted and returns reporting the designation is still not reflecting, re-run STEP 1 (parallel API calls) and route based on the updated system state.

---

## Global Fallback — Org Admin Unavailable (Any Step)

Applies whenever `get_org_admin_details` returns no result at any point in this SOP.

**[TOOL CALL]**
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

**User Message:**
> "Currently, there is no Organization Admin available to approve your request.
>
> Kindly connect with the concerned YP for further assistance:
>
> **YP Name:** [yp_name]
> **YP Email ID:** [yp_email]"

---

## SOP-P1 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| Profile already verified — badge guidance given | ❌ |
| Designation request pending — admin contact shared | ❌ |
| Designation request pending — no admin, YP contact shared | ❌ |
| No pending request — guided to re-submit | ❌ |

---
---

# SOP-P2: Leaderboard / Top Karmayogi Dashboard Not Displayed or Not Updated

## Purpose
Handle cases where a user cannot find the Leaderboard / Top Karmayogi Dashboard, or where the leaderboard data appears outdated or not updated.

---

## STEP 1 — Identify the Issue Type from User's Message

**Type:** Infer from context — no tool call required.

| Signal in Message | Issue Type | Action |
|-------------------|-----------|--------|
| "can't find", "not visible", "not showing", "where is" | Display / Navigation issue | → STEP 2 |
| "not updated", "not refreshed", "old data", "rank hasn't changed" | Update / Data freshness issue | → STEP 3 |
| Unclear | Ask: "Are you unable to locate the Leaderboard, or is it visible but showing outdated data?" |

---

## STEP 2 — Leaderboard Not Displaying → Provide Navigation Steps

**Ticket rule:** ❌ No ticket. Close after responding.

**User Message:**
> "The Leaderboard / Top Karmayogi Dashboard can be accessed by following the steps below:
>
> 1. Go to the **Home Page**.
> 2. Click on **Leader Dashboard / Leaderboard**.
> 3. You will be redirected to the **Leader Card / Top Karmayogi Card**.
>
> Please let us know if you are still unable to find the Leaderboard after following these steps."

---

## STEP 3 — Leaderboard Not Updated → Inform About Refresh Frequency

**Ticket rule:** ❌ No ticket. Close after responding.

**User Message:**
> "The Leaderboard / Top Karmayogi Dashboard is updated **once every month**, on the **1st of each month**.
>
> If your rank or learning data has not yet reflected, kindly wait until the next scheduled update on the 1st of the upcoming month.
>
> Thank you for your patience."

---

## SOP-P2 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| Leaderboard not visible — navigation steps provided | ❌ |
| Leaderboard data not updated — frequency explained | ❌ |
