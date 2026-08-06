# Agent SOP — Certificate & Progress Issue Resolution
**Platform:** iGOT Karmayogi | **Agent Framework:** LangGraph
**Applies to:** L1 AI Agent — Certificate and Program Progress Use Cases

---

## Global Agent Principles
- **Tool-first, ask-last.** Always call the relevant API before prompting the user for any information that the system can provide.
- **Infer from context.** Extract course/program names from the user's message using entity recognition and fuzzy-match against enrollment data. Only ask the user if a confident match cannot be found.
- **Single-pass diagnosis.** Fetch and analyze all relevant API data upfront. Deliver one complete, informed response rather than multiple clarification rounds.
- **Ticket only when justified.** Never raise a support ticket for incomplete content or unresolved profile edits. Tickets are reserved for confirmed sync failures or technical anomalies.

---

## Tool Registry

| Tool | Signature | Purpose |
|------|-----------|---------|
| `get_user_enrollments` | `(user_id)` | Fetch all enrolled programs/courses with status, completedOn, certificateIssued, and resource-level details |
| `get_user_profile` | `(user_id)` | Fetch the user's full name and profile details |

---

## Status Code Reference

| Code | Meaning |
|------|---------|
| `1` | In Progress / Incomplete |
| `2` | Completed |
| `null` / empty (`certificateIssued`) | Certificate not generated |

---
---

# SOP-01: Program Progress Not Updating / Program Certificate Not Generated

## Purpose
Handle issues where progress under a **program** (not a standalone course) is not updating, or the program-level certificate has not been generated after completing child courses.

---

## STEP 1 — Extract Program Name from Conversation Context

**Type:** Internal reasoning — no tool call, no user prompt.

Extract the program/course name from the user's message and store it as `inferred_program_name`. This may be `null` if no name is mentioned. Proceed immediately to STEP 2 regardless.

---

## STEP 2 — Fetch User Enrollments

**[TOOL CALL]**
```
get_user_enrollments(user_id = <session_user_id>)
```

Extract from response: `do_id`, `program_name`, `type`, `status`, `completedOn`, `certificateIssued`.

**Routing:**

| Condition | Action |
|-----------|--------|
| No enrollments returned | → STEP 3A |
| `inferred_program_name` matches exactly 1 enrollment | → STEP 4 |
| `inferred_program_name` partially matches multiple enrollments | → STEP 3B |
| `inferred_program_name` is null | → STEP 3B |
| No match found despite an inferred name | → STEP 3A |

---

## STEP 3A — No Enrollment Found

**Ticket rule:** ❌ Do not create a ticket during this validation loop.

**User Message:**
> "Upon checking your profile, we could not find any active enrollment for the program you mentioned. Kindly verify the program name and share the correct details so we can assist you further."

Re-run STEP 2 once the user provides a corrected name.

---

## STEP 3B — Confirm Program Name (Ambiguous or Not Inferred)

**User Message:**
> "We found the following enrolled program(s) under your profile. Kindly confirm which one you are referring to:
> [LIST: program_name_1, program_name_2, ...]"

Re-run STEP 2 with the confirmed name.

---

## STEP 4 — Fetch Program Hierarchy

**[TOOL CALL]**
```
get_user_enrollments(user_id = <session_user_id>)
```

Extract: Check for the completion variables likeProgram name, course names and status and completedOn. 

Proceed to STEP 5.

---

## STEP 5 — Fetch Resource-Level Completion and Diagnose

**[TOOL CALL]**
```
get_content_state(
  user_id      = <session_user_id>,
  resource_ids = <all_leaf_resource_ids_from_STEP4>
)
```

Build a diagnosis for every leaf resource by comparing both API responses:

| Enrollment API Status | Content State API Status | Diagnosis |
|----------------------|--------------------------|-----------|
| 1 | 2 | ⚠️ Sync Mismatch |
| 1 | 1 | ❌ Genuinely Incomplete |
| 2 | 2 | ✅ Complete |

Also check `certificateIssued` per child course (null/empty = certificate not generated).

**Routing based on combined findings:**

| Finding | Action |
|---------|--------|
| Any resource: Enrollment=1, ContentState=2 | → STEP 6 |
| Any resource: Enrollment=1, ContentState=1 | → STEP 7 |
| Both sync mismatch AND incomplete resources exist | → STEP 8 |
| All resources complete in both APIs, but `certificateIssued` is null | → STEP 9 |

> If multiple conditions apply, combine the relevant step messages into a single user response.

---

## STEP 6 — Sync Mismatch Found (Enrollment=1, ContentState=2)

**Ticket rule:** ✅ Escalate the ticket.

**Instruction:**
Set `"escalate": true` in your JSON decision, and include the issue details in the escalation reason.

**User Message:**
> "Upon checking, we found a synchronization issue with the following resource(s) under **[Program Name]**:
> - [Resource Name 1]
> - [Resource Name 2]
>
> We are raising this to our technical support team for investigation and resolution. You will receive an update once the issue is addressed."

---

## STEP 7 — Resources Genuinely Incomplete (Both APIs = 1)

**Ticket rule:** ❌ Do not raise a ticket. Close after responding.

**User Message:**
> "Upon checking, we found that the following resource(s) under **[Program Name]** are still pending completion:
> - [Resource Name 1]
> - [Resource Name 2]
>
> Kindly complete all pending resources to enable program completion and certificate generation."

---

## STEP 8 — Mixed: Sync Mismatch + Genuinely Incomplete

**Ticket rule:** ✅ Raise a ticket for sync-mismatch resources only. Do not raise a ticket for incomplete resources.

**Instruction:**
Set `"escalate": true` in your JSON decision, and include the issue details in the escalation reason, scoped to only the sync-mismatch resources.

**User Message:**
> "Upon checking your progress under **[Program Name]**, we found the following:
>
> **Synchronization Issue Detected:**
> The following resource(s) show a completion discrepancy that requires technical investigation:
> - [Sync Mismatch Resource Name(s)]
>
> We have raised a support ticket for these resources.
>
> **Pending Resources:**
> Additionally, the following resource(s) are still incomplete:
> - [Incomplete Resource Name(s)]
>
> Kindly complete these at your earliest convenience to ensure full program completion."

---

## STEP 9 — All Resources Complete, Certificate Not Generated

**Ticket rule:** ✅ Escalate the ticket.

**Instruction:**
Set `"escalate": true` in your JSON decision, and include the issue details in the escalation reason.

**User Message:**
> "Upon checking, we found that all resources under **[Program Name]** have been successfully completed; however, the program certificate has not yet been generated. This indicates a synchronization issue at the certificate generation stage.
>
> We have escalated this to our technical team for further investigation. You will be notified once it is resolved.
>
> Thank you,
> Karmayogi Bharat Support Team"

---

## SOP-01 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| User not enrolled / wrong program name | ❌ |
| Resources genuinely incomplete | ❌ |
| Sync mismatch: Enrollment API=1, Content State API=2 | ✅ |
| All resources complete but certificate not issued | ✅ |

---
---

# SOP-02: Course Certificate Not Generated / Unable to Download

## Purpose
Resolve issues where a **standalone course** (not a program) has been completed but the certificate has not been generated, is missing, or cannot be downloaded.

---

## STEP 1 — Extract Course Name from Conversation Context

**Type:** Internal reasoning — no tool call, no user prompt.

Parse the user's message for a course name and store it as `inferred_course_name` (may be `null`). Proceed immediately to STEP 2.

---

## STEP 2 — Fetch User Enrollments

**[TOOL CALL]**
```
get_user_enrollments(user_id = <session_user_id>)
```

Extract from response: `do_id`, `course_name`, `status`, `completedOn`, `certificateIssued`, `resources[]`.

**Routing:**

| Condition | Action |
|-----------|--------|
| No enrollments returned | → STEP 3A |
| `inferred_course_name` matches exactly 1 enrollment | → STEP 4 |
| `inferred_course_name` partially matches multiple enrollments | → STEP 3B |
| `inferred_course_name` is null | → STEP 3B |
| No match found despite an inferred name | → STEP 3A |

---

## STEP 3A — No Enrollment Found for Mentioned Course

**Ticket rule:** ❌ Do not create a ticket during name resolution.

**User Message:**
> "Upon checking your profile, we could not find any enrollment for the course you mentioned. Kindly verify the course name and share the exact name as displayed on the portal so we can look into this further."

**If the user is still unable to provide the exact course name:**

> "Here is a list of courses currently enrolled under your profile:
> [LIST: course_name_1, course_name_2, ...]
>
> Kindly refer to this list and confirm the correct course name."

Re-run STEP 2 once a confirmed name is provided.

> **Agent note:** When presenting the list, prioritize courses with `status=2` (Completed) at the top, as these are most likely relevant to a certificate complaint.

---

## STEP 3B — Confirm Course Name (Ambiguous or Not Inferred)

**User Message:**
> "We found the following course(s) enrolled under your profile. Kindly confirm which one you are referring to:
> [LIST: course_name_1 (Completed), course_name_2 (In Progress), ...]"

Re-run STEP 2 with the confirmed course name.

---

## STEP 4 — Check Completion Status

From the matched enrollment record:

| Condition | Action |
|-----------|--------|
| `status = 2` AND `completedOn` is available | → STEP 5 |
| `status = 2` AND `completedOn` is null | → Treat as > 24 hours; GO TO STEP 6A |
| `status = 1` (In Progress) | → STEP 7 |

---

## STEP 5 — Compute Time Elapsed Since Completion

Compute: `hours_since_completion = now() - completedOn` (in hours)

| Condition | Action |
|-----------|--------|
| `hours_since_completion > 24` | → STEP 6A |
| `hours_since_completion ≤ 24` | → STEP 6B |

---

## STEP 6A — Course Completed, 24+ Hours Elapsed → Guide Download

**User Message:**
> "Upon checking, we can confirm that you have successfully completed **[Course Name]** (completed on: [completedOn date]).
>
> Since more than 24 hours have passed since completion, the certificate should be available for download. Kindly follow the steps below:
>
> 1. Log in to the **iGOT Karmayogi Portal**.
> 2. Go to **Profile**.
> 3. Click on **My Learnings**.
> 4. Search for and select **[Course Name]**.
> 5. Open the course details page.
> 6. Navigate to the **About** section.
> 7. Click **Download Certificate**.
>
> Please let us know if you are still unable to download the certificate after following these steps."

**Next:** If user confirms they are still unable to download → GO TO STEP 6C.

---

## STEP 6B — Course Completed, Less Than 24 Hours Elapsed

**Ticket rule:** ❌ No ticket. Close after responding.

**User Message:**
> "Upon checking, we can confirm that you have successfully completed **[Course Name]** (completed on: [completedOn date]).
>
> Certificate generation typically takes up to **24 hours** after course completion. Kindly wait until [completedOn + 24 hours] and then try downloading the certificate from your profile under **My Learnings → [Course Name] → About → Download Certificate**.
>
> If the certificate is still unavailable after 24 hours, please reach out and we will investigate further."

---

## STEP 6C — User Still Unable to Download After Following Steps

**Ticket rule:** ✅ Escalate the ticket.

**Instruction:**
Set `"escalate": true` in your JSON decision, and include the issue details in the escalation reason.

**User Message:**
> "We have created a support ticket for further investigation. Our technical team will review the issue and get back to you with an update.
>
> Thank you,
> Karmayogi Bharat Support Team"

---

## STEP 7 — Course Not Yet Completed (Status = 1)

**Ticket rule:** ❌ Do not raise a ticket. Close after responding.

From the enrollment response, identify all resources where `status ≠ 2`.

**User Message:**
> "Upon checking, we found that **[Course Name]** is currently **in progress** and has not yet been fully completed.
>
> The following resource(s) are still pending:
> - [Resource Name 1]
> - [Resource Name 2]
>
> Kindly complete all pending resources to generate the certificate. Here is how to identify and complete them:
>
> 1. Go to **Profile**.
> 2. Click on **My Learning**.
> 3. Open the **In Progress** section under Contents.
> 4. Locate and select **[Course Name]**.
> 5. Click **Resume**.
> 6. Expand each module using the **(+)** icon.
> 7. Look for content items **without a blue tick mark** — these are incomplete.
> 8. Complete all pending videos, assessments, or content items until every item shows a blue tick.
> 9. Ensure overall course progress reaches **100%**.
>
> Once all resources are completed, the certificate will be generated within 24 hours."

---

## SOP-02 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| No enrollment found / wrong course name | ❌ |
| Course completed less than 24 hours ago | ❌ |
| Course still in progress | ❌ |
| Course completed > 24 hours ago, download steps failed | ✅ |

---
---

# SOP-03: Incorrect Name on Certificate

## Purpose
Resolve issues where the name displayed on a downloaded certificate is incorrect or does not match the user's actual name.

---

## STEP 1 — Fetch User Profile (Immediate — No User Prompt)

**[TOOL CALL]**
```
get_user_profile(user_id = <session_user_id>)
```

Extract: `profile.full_name`

> The `full_name` field from this API is the single authoritative source for what appears on all generated certificates. Do not ask the user what name they see on the certificate before making this call.

Proceed immediately to STEP 2.

---

## STEP 2 — Present Fetched Name and Confirm with User

**User Message:**
> "Upon checking your profile, we found that your current registered name is:
>
> **Name: [profile.full_name]**
>
> Could you please confirm whether this name is correct?"

**Routing based on user response:**

| User Response | Action |
|--------------|--------|
| Name is **correct** | → STEP 3 |
| Name is **incorrect** | → STEP 4 |

---

## STEP 3 — Profile Name is Correct → Guide Re-download

**User Message:**
> "Since your profile name is correct, the certificate should reflect **[profile.full_name]**. Kindly re-download the certificate by following the steps below:
>
> 1. Log in to your **iGOT Karmayogi** account.
> 2. Go to **Profile**.
> 3. Open the **Certificates** section.
> 4. Locate the required certificate.
> 5. Click **Download Certificate**.
> 6. Verify that the downloaded certificate displays the correct name.
>
> Please let us know if the name still appears incorrect after re-downloading."

**Routing after re-download:**

| User Confirms | Action |
|--------------|--------|
| Certificate now shows correct name | ❌ No ticket. Close politely. |
| Certificate still shows wrong name despite correct profile | ✅ Raise ticket — this is a technical rendering issue. |

**If escalation required:**
Set `"escalate": true` in your JSON decision, and include the issue details (profile name, reported cert name) in the escalation reason.

---

## STEP 4 — Profile Name is Incorrect → Ask About Update

**User Message:**
> "The name displayed on your certificate is generated directly from your **profile name** on the iGOT Karmayogi portal.
>
> Since your profile name appears to be incorrect, you would need to update it for the certificate to reflect the correct name.
>
> Would you like to update your profile name?"

**Routing based on user response:**

| User Response | Action |
|--------------|--------|
| **Yes**, wants to update | → STEP 5A |
| **No**, does not want to update | → STEP 5B |

---

## STEP 5A — User Wants to Update Profile Name

**Ticket rule:** ❌ No ticket required. Close after guiding.

**User Message:**
> "Please follow the steps below to update your profile name:
>
> 1. Log in to your **iGOT Karmayogi** account.
> 2. Go to **Profile**.
> 3. Click on **Edit Profile**.
> 4. Update the **Name** field with your correct name.
> 5. Save the changes.
>
> Once your profile is updated, please re-download the certificate:
>
> 1. Go to **Profile**.
> 2. Open the **Certificates** section.
> 3. Locate the required certificate.
> 4. Click **Download Certificate**.
> 5. Verify that the updated name is now reflected on the certificate.
>
> Please let us know if you need any further assistance."

---

## STEP 5B — User Does Not Want to Update Profile Name

**Ticket rule:** ❌ No ticket required. Close politely.

**User Message:**
> "Understood. No changes will be made to your profile.
>
> Please note that the name on the certificate will continue to reflect your current profile name: **[profile.full_name]**.
>
> If you change your mind in the future, you can update your profile name at any time by going to **Profile → Edit Profile** and then re-download the certificate to see the updated name.
>
> Feel free to reach out if you need any further assistance."

---

## SOP-03 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| Profile name is correct, re-download resolves the issue | ❌ |
| Profile name is incorrect, user guided to update | ❌ |
| Profile name is correct but certificate still shows wrong name after re-download | ✅ |
