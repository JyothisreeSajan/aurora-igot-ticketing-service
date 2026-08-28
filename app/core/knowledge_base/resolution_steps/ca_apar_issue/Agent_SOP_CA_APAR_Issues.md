# Agent SOP — Training Plan / APAR Courses Not Visible in Profile
**Platform:** iGOT Karmayogi | **Agent Framework:** LangGraph
**Applies to:** L1 AI Agent — Training Plan / APAR Course Visibility Use Cases

---

## Global Agent Principles
- **Tool-first.** Fetch the user's CBP plan and profile status via API immediately.
- **Never commit to approval timelines** for MDO/YP-driven assignments.

---

## Tool Registry

| Tool | Signature | Purpose |
|------|-----------|---------|
| `get_user_profile` | `(email)` | Fetch organization, profile verification status, designation, group, ministry/state |
| `get_user_cbp_plan` | `(email)` | Fetch the user's CBP (training) plan — list of assigned courses with `isApar`, `endDate` |
| `get_user_enrollments` | `(email, status_filter=None, content_id=None)` | Fetch enrollment/progress status for the user's assigned courses. Pass `content_id` to check one specific course across the full enrollment history (bypasses the top-20-most-recent cap used when no `content_id` is given) — returns just `course_name` and `completed: true/false` for that course |
| `get_mdo_details` | `(email)` | Fetch MDO Admin/Leader name and email for the user's organization |
| `get_yp_am_details` | `(ministry_or_state)` | Fetch YP/AM (SPOC) name, email, and contact details |
| `get_user_cap_assignment` | `(email)` | Fetch the user's assigned Comprehensive Assessment Program(s) (CAP) — returns `total_count` and `assignments` (each: `cap_id`, `cap_name`, `end_date`, `link`, `link_html`) |

---
---

# SOP-1: Training Plan / APAR Courses Not Visible in Profile

## Purpose
Handle cases where a user reports that their Training Plan / APAR courses are not visible,
that unexpected courses appear, or that the wrong plan/courses were assigned.

---

## STEP 1 — Fetch the User's CBP Plan

**[TOOL CALL]**
```
get_user_cbp_plan(email = <user_email>)
```

**If `found: false`** (profile could not be located). Resolved. Close.
> "We could not verify your profile at this time; please try again later."
Do not proceed to any other step.

**Otherwise, route based on result:**

| CBP Plan Data | Action |
|----------------|--------|
| Exists (total_count > 0) | → STEP 2 |
| Does not exist (total_count = 0) | → STEP 3 |

---

## STEP 2 — CBP Plan Data Exists → Summarize and Guide

**[TOOL CALL]**
```
get_user_enrollments(email = <user_email>)
```

**Outcome:** Resolved — respond and close.

**User Message:**
> "Upon checking, we found that the training plan has been assigned to your profile.
>
> To view the training plan courses, please follow the steps below:
> 1. Navigate to the **Homepage** and locate the **My iGOT** section.
> 2. Under **My iGOT**, click on the **APAR** tab — courses marked with a green APAR tag.
> 3. Click **Upcoming** to view all upcoming courses (APAR and non-APAR).
> 4. Click **All** to view all assigned courses.
> 5. Click **Completed** to view courses you've already finished."

---

## STEP 3 — No CBP Plan Data Exists → Validate Profile

**[TOOL CALL]**
```
get_user_profile(email = <user_email>)
```

**Route based on organization:**

| Organization | Action |
|--------------|--------|
| Mapped to **"iGOT"** or **"Karmayogi Prarambh Trainee"** (hardcoded check) | → STEP 3A |
| Mapped to any other real organization | → STEP 4 |

---

## STEP 3A — Mapped to iGOT / Karmayogi Prarambh Trainee → Guide Transfer Request

**Outcome:** Resolved — close after guiding.

**User Message:**
> "Upon checking, we found that your profile is currently mapped to iGOT/Karmayogi Prarambh Trainee.
>
> APAR courses and training plans are assigned only after a user is mapped to their respective department/organization. As your profile is not yet mapped to the correct department/organization, no APAR courses or training plans can be assigned at this stage.
>
> Kindly raise a Transfer Request to move your profile to the correct department/organization. Once the transfer request is approved and your profile is mapped to the appropriate organization, the concerned authority will be able to assign the relevant training plans and courses.
>
> To raise a Transfer Request:
> 1. Click on the **Profile Icon** (top-right corner) → **View Profile** → **Make Transfer Request**.
> 2. Select the correct Organization Name, Group, and Designation.
> 3. Click **Submit** — the request is sent to the concerned MDO Admin for approval.
>
> Once your profile is mapped to the correct organization, kindly recheck the APAR/training plan section."

---

## STEP 4 — Mapped to a Real Organization → Check Profile Verification

**Route based on `profile_status`:**

| Profile Status | Action |
|-----------------|--------|
| Verified | → STEP 5 |
| Not Verified | → STEP 6 |

---

## STEP 5 — Profile Verified → Share Assigning-Authority Contact

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If MDO details are not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

Both tools return the contact's name/email as **masked placeholder tokens** (e.g.
`{{MDO_ADMIN_NAME}}`, `{{MDO_ADMIN_EMAIL}}`, `{{YP_AM_NAME}}`, `{{YP_AM_EMAIL}}`) — not real
values, the same way user emails are masked elsewhere. Copy the tokens exactly as
returned, character for character, curly braces included. Never invent, guess, or
paraphrase a name/email — the real value is substituted in automatically after the
response is generated, but only if the token text matches exactly.

**Outcome:** Resolved, if either contact is found. Escalate only if **neither** an MDO Admin **nor** a YP/SPOC can be found.

**User Message (contact found):**
> "Upon checking, we found that no CBP/APAR training plan is currently assigned to your profile. The concerned authority is responsible for assigning training plans and courses.
>
> Kindly connect with the contact below for further assistance:
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

**User Message (neither contact found — escalate):**
> Tell the user: their issue has been logged and escalated to the support team, and a specialist will assist them shortly.

---

## STEP 6 — Profile Not Verified → Guide Profile Verification

**Outcome:** Resolved — close after guiding.

**User Message:**
> "As your profile is currently not verified, we request you to complete the profile verification process. Once your profile is successfully verified, the training plans/APAR courses may reflect in your account.
>
> Kindly recheck the APAR section after the profile verification is completed."

---

## Edge Case 1 — Particular Course Not Visible in CBP Plan / Additional or Unexpected Course Visible

The course name is already in the user's message — extract it directly, do not ask for it.
Match it against `course_name` values case-insensitively and by partial match in either
direction (the user's wording may be contained in the real course name, or vice versa).

**[TOOL CALL]**
```
get_user_cbp_plan(email = <user_email>)
```
Validate whether the named course exists in the user's assigned CBP plan, and read its
`content_id` if it does.

---

### If the course EXISTS in the plan

**[TOOL CALL]**
```
get_user_enrollments(email = <user_email>, content_id = <the plan course's content_id>)
```
Validate whether the course has already been completed.

**Always pass `content_id`** when checking one specific course. Calling
`get_user_enrollments` without it only returns the 20 most recently enrolled courses; an
older enrollment can fall outside that window and get misread as not completed even though
the user has actually finished it.

**`completed: true`.** Resolved. Close. Use EXACTLY this message (fill in `[course_name]`):
> "Upon checking, we found that the course '[course_name]' is already part of your training plan and has been successfully completed.
>
> To view the completed course, please follow the steps below:
> 1. Navigate to the Homepage.
> 2. Locate the My iGOT section.
> 3. Click on the Completed tab to view all completed courses.
> 4. Search for '[course_name]' in the list of completed courses.
>
> You should be able to find and access the completed course there."

Close the conversation politely.

**`completed: false`.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that the course '[course_name]' is already available in your assigned training plan.
>
> To view the mentioned courses, please follow the steps below:
> 1. Navigate to the Homepage and locate the My iGOT section.
> 2. Under My iGOT, click on the APAR tab. Here, you will be able to view all APAR courses, which are marked with a green APAR tag.
> 3. Click on the Upcoming tab to view all upcoming courses, including both APAR and non-APAR courses.
> 4. Click on the All tab to view all assigned courses, including both APAR and non-APAR courses.
> 5. To view completed courses, click on the Completed tab and review the courses that have been completed."

Close the conversation politely.

---

### If NO CBP plan exists at all

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If MDO details are not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

Both tools return the contact's name/email as **masked placeholder tokens** (e.g.
`{{MDO_ADMIN_NAME}}`, `{{MDO_ADMIN_EMAIL}}`, `{{YP_AM_NAME}}`, `{{YP_AM_EMAIL}}`) — copy them
exactly as returned; never invent, guess, or paraphrase a name/email yourself.

**If either contact found.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that no CBP/training plan is currently assigned to your profile.
>
> Training plans and courses are assigned by the concerned authority.
>
> Kindly connect with the contact details below for further assistance:
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

Close the conversation politely.

**If neither contact is found**, escalate natively (set `escalate=true`). Tell the user their
issue has been logged and escalated to the support team, and a specialist will assist them
shortly.

---

### If the course does NOT exist in the plan (a plan exists, just not this course)

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
Fetch MDO Name and Email based on the user's ministry/state/organization. If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```
Same masked-placeholder-token handling as above.

**If either contact found.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that the requested course is currently not available in your assigned training/CBP plan.
>
> Training plans and course assignments are managed by the concerned authority.
>
> We request you to kindly connect with the contact details below for further assistance regarding course allocation/addition:
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

Close the conversation politely.

**If neither contact is found**, escalate natively (set `escalate=true`). Tell the user their
issue has been logged and escalated to the support team, and a specialist will assist them
shortly.

---

**No specific course named in the message** → Follow the STEP 2 pattern (full plan summary).

**Never invent a clickable link/URL for the course** — none of the tools return one; only use the named navigation steps above.

---

## Edge Case 2 — Wrong CBP Plan / Wrong Courses Assigned

**[TOOL CALL]**
```
get_user_profile(email = <user_email>)
```
**One single call.** Use this same result for both the profile-verification check and the
organization/designation/group details below — do not call any other profile-fetching tool
for this Edge Case.

**If profile is NOT verified** → follow STEP 6 (guide profile verification).

**If profile IS verified:**

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```
Both tools return the contact's name/email as **masked placeholder tokens** (e.g.
`{{MDO_ADMIN_NAME}}`, `{{MDO_ADMIN_EMAIL}}`, `{{YP_AM_NAME}}`, `{{YP_AM_EMAIL}}`) — copy them
exactly as returned; never invent, guess, or paraphrase a name/email yourself.

**User Message** (if either contact found — resolved, close):
> "As the training plans/courses are managed and assigned by the concerned authority, we request you to kindly connect with the contact details below for further assistance:
>
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

If neither contact is found, escalate natively (set `escalate=true`). Tell the user their
issue has been logged and escalated to the support team, and a specialist will assist them
shortly.

---

## SOP-1 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| CBP plan exists — summary shared | ❌ |
| Mapped to iGOT/Karmayogi Prarambh Trainee — guided to raise Transfer Request | ❌ |
| No plan, contact (MDO or YP) found | ❌ |
| No plan, neither MDO nor YP found | ✅ |
| Profile not verified — guided to verify | ❌ |
| Edge Case 1/2 — contact found | ❌ |
| Edge Case 1/2 — neither MDO nor YP found | ✅ |

---
---

# SOP-2: Comprehensive Assessment Program (CAP) Issues

## Purpose
Handle cases where a user reports that their Comprehensive Assessment Program (CAP) is not
visible, or that they are unable to enroll in / have an eligibility issue with / believe an
incorrect CAP has been assigned.

Covers two subcategories:
- **Comprehensive Assessment Not Visible**
- **Comprehensive Assessment Unable to enroll**

---

## Comprehensive Assessment Not Visible

### STEP 1 — Verify Profile Status

**[TOOL CALL]**
```
get_user_profile(email = <user_email>)
```

**If profile is NOT verified.** Resolved. Close. Use EXACTLY this message:
> "As your profile is currently not verified, we request you to complete the pending profile verification. Once your profile is verified, kindly check the Comprehensive Assessment Program (CAP) again."

**If profile IS verified**, check whether `cadreDetails`, `serviceDetails`, `batch`, and
`centralDeputation` are populated on that same `get_user_profile` result — this is a
background check; never ask the user whether they belong to an All India Service.

**Some of the four fields populated but not all (partially complete).** Resolved. Close.
Use EXACTLY this message:
> "We found that the following mandatory service-related details are missing from your profile:
>
> - Cadre Details
> - Service Details
> - Batch Information
> - Central Deputation Details
>
> Kindly update the required details in your profile under the Other Details section. Once the profile is updated successfully, the APAR plans may reflect in your account within 4 hours."

**All four fields empty, OR all four fields populated** → STEP 1A.

---

### STEP 1A — Check if User Named a Specific Wrong Field

Read the user's own message directly — never ask. If the user explicitly states that a
specific field is wrong (Organization/Department, Designation, Group, Cadre, Service, or
Batch), use the matching update-flow message from SOP-1 STEP 4A (below) — reproduce it
exactly, do not write a different version here.

- Organization/Department incorrect → the **Transfer Request** message
- Designation incorrect → the **Designation Update** message
- Group/Cadre/Service/Batch incorrect → the **Profile Update** message

> **Transfer Request message:**
> "To correct your organization mapping, please raise a Transfer Request:
>
> 1. Log in to iGOT Karmayogi.
> 2. Click on the Profile Icon (top-right corner).
> 3. Click on View Profile.
> 4. Select Make Transfer Request.
> 5. Select the correct Organization Name from the dropdown.
> 6. Choose your Group and Designation (if applicable).
> 7. Ensure all details are accurate and click Submit.
>
> Once approved, kindly recheck the APAR/Training Plan section on your profile."

> **Designation Update message:**
> "To update your designation, please follow the steps below:
>
> 1. Log in to iGOT Karmayogi.
> 2. Click on View Profile from the top-right menu.
> 3. Navigate to Primary Details and click the Edit icon.
> 4. Update the correct Designation.
> 5. Click Send for Approval to submit the changes — the request will be sent to your MDO Admin for approval.
>
> Once approved, kindly recheck the APAR/Training Plan section on your profile."

> **Profile Update message:**
> "To update your Group or other profile details, please follow the steps below:
>
> 1. Log in to iGOT Karmayogi.
> 2. Click on View Profile from the top-right menu.
> 3. Navigate to Primary Details and click the Edit icon.
> 4. Update the correct Group and any other required details.
> 5. Click Send for Approval to submit the changes — the request will be sent to your MDO Admin for approval.
>
> Once approved, kindly recheck the APAR/Training Plan section on your profile."

**If the message does not name a specific field** → STEP 2.

---

### STEP 2 — Verify CAP Assignment

**[TOOL CALL]**
```
get_user_cap_assignment(email = <user_email>)
```

Check whether the user's message names a specific CAP. Match it against `cap_name` values
case-insensitively and by partial match in either direction (same approach as Edge Case 1's
course-name matching).

**Named CAP IS found in assignments.** Resolved. Close. Use EXACTLY this message (copy
`link_html` verbatim for the link — it's already a complete clickable HTML tag, never
rewrite it or use the plain `link` field):
> "Upon checking, we found that the Comprehensive Assessment Program (CAP) '[cap_name]' is already assigned to your profile.
>
> You can access it directly using the link below:
> [link_html]"

**Named CAP is NOT found in assignments** → STEP 3, using the named CAP in the message
instead of a generic reference.

**No specific CAP named:**
- `total_count = 0` → STEP 3 (no CAP assigned at all — valid regardless of which CAP they meant)
- `total_count >= 1` → do **not** assume this is the CAP they meant, even if there's only one — they may be asking about something unrelated to their own assignments. Set `needs_clarification=true`. Use EXACTLY this message (never mention how many CAPs are/aren't assigned — that's internal, not something to tell the user):
> "We understand your concern regarding the visibility of your Comprehensive Assessment Program (CAP).
>
> To help us investigate this further, kindly share the following details:
> - CAP Name
> - CAP Link"

---

### STEP 3 — No CAP Assigned → Share Assigning-Authority Contact

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```
Same masked-placeholder-token handling as SOP-1.

**If either contact found.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that no Comprehensive Assessment Program (CAP) is currently assigned to your profile.
>
> CAP creation and assignment are managed by the concerned department.
>
> Kindly connect with the below contact for further assistance regarding CAP creation and assignment:
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

**If neither contact is found**, escalate natively (set `escalate=true`). Tell the user their
issue has been logged and escalated to the support team, and a specialist will assist them
shortly.

---

## Comprehensive Assessment Unable to Enroll / Eligibility-Related Issues / Incorrect CAP Assigned

Same STEP 1 / STEP 1A logic as "Comprehensive Assessment Not Visible" above (profile
verification, background AIS-field check, named-wrong-field routing) — reused as-is, not
duplicated here.

### Verify CAP Assignment

**[TOOL CALL]**
```
get_user_cap_assignment(email = <user_email>)
```

Same case-insensitive/partial `cap_name` matching as above.

**Named CAP IS found in assignments.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that the following Comprehensive Assessment Program (CAP) is assigned to your profile:
>
> CAP Name: [cap_name]
> CAP Link: [link_html]
>
> Kindly use the above link to access your Comprehensive Assessment Program."

**Named CAP is NOT found in assignments** (the CAP they're asking about is not actually
theirs):

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

**If either contact found.** Resolved. Close. Use EXACTLY this message:
> "CAP assignment is managed by the concerned department.
>
> If you believe that an incorrect Comprehensive Assessment Program (CAP) has been assigned to your profile, kindly connect with your department MDO for further verification and necessary changes.
>
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

**If neither contact is found**, escalate natively (set `escalate=true`). Same escalation
message as above.

**No specific CAP named:**
- `total_count = 0` → No CAP Assigned (below)
- `total_count >= 1` → do not assume this is the CAP they meant. Set `needs_clarification=true`. Use EXACTLY this message:
> "We understand your concern regarding enrolling in your Comprehensive Assessment Program (CAP).
>
> To help us investigate this further, kindly share the following details:
> - CAP Name
> - CAP Link"

### No CAP Assigned → Share Assigning-Authority Contact

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

**If either contact found.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that no Comprehensive Assessment Program (CAP) is currently assigned to your profile.
>
> Comprehensive Assessment Programs (CAPs) are created and assigned by the concerned department.
>
> Kindly connect with the below contact for further assistance regarding CAP creation or assignment:
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

**If neither contact is found**, escalate natively (set `escalate=true`). Tell the user their
issue has been logged and escalated to the support team, and a specialist will assist them
shortly.

---

## SOP-2 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| Named CAP found — name/link shared | ❌ |
| No CAP named, CAPs assigned — clarification requested | ❌ (stays open) |
| No CAP assigned, contact (MDO or YP) found | ❌ |
| No CAP assigned, neither MDO nor YP found | ✅ |
| Named CAP not found, contact (MDO or YP) found | ❌ |
| Named CAP not found, neither MDO nor YP found | ✅ |
| AIS fields partially complete — guided to update | ❌ |
