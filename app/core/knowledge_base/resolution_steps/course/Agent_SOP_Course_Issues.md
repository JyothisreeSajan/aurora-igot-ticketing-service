# Agent SOP — Course Issues Resolution
**Platform:** iGOT Karmayogi | **Agent Framework:** LangGraph
**Applies to:** L1 AI Agent — Course, Program, and Event Use Cases

---

## Global Agent Principles
- **Tool-first, ask-last.** Call APIs immediately using information already available in the session and the user's message. Never ask the user for data the system can provide.
- **Infer from context.** Extract course/program/event names, issue types, and intent from the user's message before deciding whether to prompt for clarification.
- **Single-pass diagnosis.** Fetch and analyze all relevant data in one flow. Deliver one complete, informed response.
- **Ticket only when justified.** Tickets are raised only for confirmed technical failures — not for ineligibility, incomplete content, or missing profile data that the user or MDO can resolve.

---

## Tool Registry

| Tool | Signature | Purpose |
|------|-----------|---------|
| `composite_content_search` | `(query, type, threshold=0.90)` | Search for courses, programs, or events by name with semantic similarity |
| `get_access_settings` | `(content_id)` | Fetch access/eligibility restrictions configured for a course or event |
| `get_user_profile` | `(user_id)` | Fetch user profile attributes for eligibility comparison |
| `get_user_enrollments` | `(user_id)` | Fetch all enrolled courses/programs/events with status, progress, and resource details |
| `get_content_metadata` | `(content_id)` | Fetch resource type, URLs (streaming, registration, artifact), and configuration details |
| `get_mdo_details` | `(org_id)` | Fetch MDO name and email ID for the user's organization |
| `get_yp_am_details` | `(ministry_or_state)` | Fetch YP/AM name, email, and contact details |
| `get_user_feed` | `(user_id)` | Fetch eHRMS ID, External System Name, and integration mapping fields |
| `get_apar_assignments` | `(user_id)` | Verify APAR/CAP course and assessment assignments |

---

## Status Code Reference

| Code | Meaning |
|------|---------|
| `1` | In Progress |
| `2` | Completed |
| `LIVE` | Course/Event is active and available |
| `RETIRED` | Course/Event is no longer available |
| `DRAFT` / `UNDER REVIEW` | Course/Event is not yet published |

---
---

# SOP-C1: User Unable to Find / Enroll in a Course, Program, or Event

## Purpose
Handle cases where a user cannot find a specific course, program, or event on the platform, or is unable to enroll in one.

---

## STEP 1 — Infer Intent from User Message

**Type:** Internal reasoning — no tool call, no user prompt.

Analyze the user's message to determine:

| Signal in Message | Inferred Type |
|-------------------|---------------|
| Mentions "Coursera", "Harvard", "edX", "external", "marketplace" | `marketplace_course` → GO TO STEP 2 |
| Mentions "event", "webinar", "session", "seminar" | `event` → GO TO STEP 8 |
| Mentions "course", "program", "module", or no qualifier | `course_or_program` → GO TO STEP 3 |
| Cannot determine | Ask: "Are you looking for a course/program or an event?" — then route accordingly |

Extract `inferred_content_name` from the message (may be `null`).

---

## STEP 2 — Marketplace / External Course Request

**Ticket rule:** ❌ No ticket required. Close after responding.

**User Message:**
> "Thank you for reaching out. Marketplace and external courses (such as those from Coursera or Harvard) are offered through a separate enrollment cycle.
>
> Currently, there is no active enrollment cycle for marketplace/external courses. Kindly wait until the next enrollment cycle is announced — you will be notified when the process begins.
>
> Thank you for your interest in learning through the platform."

---

## STEP 3 — Search for Course / Program

**[TOOL CALL]**
```
composite_content_search(
  query     = <inferred_content_name or user-provided name>,
  type      = "course_or_program",
  threshold = 0.90
)
```

**Routing:**

| Result | Action |
|--------|--------|
| No results returned | → STEP 4 |
| Exactly 1 result returned | → STEP 5 |
| Multiple results returned | → STEP 7 |

---

## STEP 4 — No Course / Program Found

**Ticket rule:** ❌ No ticket during this validation loop.

**User Message:**
> "We were unable to find the course or program you mentioned in our system. Kindly share the exact name as displayed on the portal so we can search again."

Re-run STEP 3 with the corrected name once the user responds.

---

## STEP 5 — Single Course / Program Found → Validate Status and Eligibility

**[TOOL CALL — Run in parallel]**
```
get_access_settings(content_id = <result.content_id>)
get_user_profile(user_id = <session_user_id>)
```

**Check course status first:**

| Course Status | Action |
|---------------|--------|
| `LIVE` | → STEP 6 (Eligibility check) |
| `RETIRED` | Inform user: course is retired and no longer available. Close. |
| `DRAFT` / `UNDER REVIEW` / any other | Inform user: course is under review and temporarily unavailable. Close. |

---

## STEP 6 — Eligibility Check (Single Result, LIVE)

**Check if the course is a moderated course** (from content metadata).

**If NOT a moderated course:**

Compare `get_access_settings` response against `get_user_profile` attributes.

| Access Settings | Eligibility Result | Action |
|----------------|--------------------|--------|
| No access settings configured | Publicly accessible — user is eligible | → STEP 6A (Share course link) |
| Access settings found, user profile matches | User is eligible | → STEP 6A (Share course link) |
| Access settings found, user profile does not match | User is not eligible | → STEP 6B (Share MDO/YP contact) |

**If IS a moderated course:**

Validate BOTH content metadata eligibility AND access settings criteria. The user is eligible only if both checks pass.

| Metadata Match | Access Settings Match | Action |
|---------------|----------------------|--------|
| ✅ | ✅ (or not configured) | → STEP 6A (Share course link) |
| ❌ (either check fails) | — | → STEP 6B (Share MDO/YP contact) |

---

## STEP 6A — User is Eligible → Share Course Link

**User Message:**
> "Upon checking, we found the requested course on the platform and confirmed that you are eligible to enroll.
>
> **Course Name:** [course_name] — [course_hyperlink]
>
> Kindly use the above link to navigate to the course and proceed with enrollment."

**Ticket rule:** ❌ No ticket. Close politely.

---

## STEP 6B — User is Not Eligible → Share MDO / YP Contact

**[TOOL CALL]**
```
get_mdo_details(org_id = <user_profile.org_id>)
```

If MDO details are not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

**User Message:**
> "Upon checking, we found that the course access criteria does not match your current profile attributes.
>
> We request you to connect with the concerned MDO/YP for further assistance regarding course eligibility and access.
>
> **Contact Details:**
> Name: [mdo_or_yp_name]
> Email ID: [mdo_or_yp_email]"

**Ticket rule:** ❌ No ticket. Close politely.

---

## STEP 7 — Multiple Courses / Programs Found → Filter by Eligibility

**[TOOL CALL — For each result]**
```
get_access_settings(content_id = <result.content_id>)  // for each course in results
get_user_profile(user_id = <session_user_id>)           // once, reuse across all
```

**Build eligible course list:**
- Include: LIVE courses where user is eligible (access settings match or not configured)
- Exclude: RETIRED courses, ineligible courses (access settings do not match)

**If eligible courses exist:**

**User Message:**
> "We found multiple courses matching your search on the platform. Below are the ones you are eligible to enroll in:
>
> | Course Name | Provider | Status |
> |-------------|----------|--------|
> | [course_name_1] — [link] | [provider] | LIVE |
> | [course_name_2] — [link] | [provider] | LIVE |
>
> Kindly select the appropriate course as per your requirement."

**If NO eligible courses exist:**

Run `get_mdo_details` / `get_yp_am_details` and deliver STEP 6B message.

---

## STEP 8 — Search for Event

**[TOOL CALL]**
```
composite_content_search(
  query     = <inferred_content_name or user-provided name>,
  type      = "event",
  threshold = 0.90
)
```

**Routing:**

| Result | Action |
|--------|--------|
| No results returned | Ask user for exact event name and re-run search |
| Event found | → STEP 9 |

**Ticket rule:** ❌ No ticket during name resolution loop.

---

## STEP 9 — Event Found → Validate Status and Eligibility

**[TOOL CALL — Run in parallel]**
```
get_access_settings(content_id = <event.content_id>)
get_user_profile(user_id = <session_user_id>)
```

| Event Status | Eligibility | Action |
|--------------|-------------|--------|
| `LIVE` | User eligible (access settings match or not configured) | Share event name and hyperlink. Close. |
| `LIVE` | User not eligible | Run `get_mdo_details` / `get_yp_am_details`. Share contact. Close. |
| Not `LIVE` | — | Inform user the event is currently unavailable. Close. |

**User Message (Eligible):**
> "Upon checking, we found the requested event on the platform and confirmed that you are eligible to participate.
>
> **Event Name:** [event_name] — [event_hyperlink]
>
> Kindly use the above link to access the event and proceed accordingly."

**User Message (Not Eligible):**
> "Upon checking, we found that the event access criteria does not match your current profile attributes.
>
> We request you to connect with the concerned MDO/YP for further assistance.
>
> **Contact Details:**
> Name: [mdo_or_yp_name]
> Email ID: [mdo_or_yp_email]"

---

## Course Visibility Rule (Applied Globally)
- Always call `get_access_settings` for every course or event identified through search.
- If access settings are configured → compare with user profile. Share link only if eligible.
- If access settings return empty / null → treat as publicly accessible. Share link directly.
- Never display course/event links to ineligible users.
- If ineligible → always provide MDO/YP contact details.

---

## SOP-C1 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| Marketplace/external course query | ❌ |
| Course/event not found in search | ❌ |
| Course retired or under review | ❌ |
| User ineligible for course or event | ❌ |
| User eligible — course/event shared | ❌ |

---
---

# SOP-C2: Course / Program / Event Progress Not Updating

## Purpose
Resolve cases where a user's learning progress is stuck, not updating, or not reflecting correctly under a course, program, or event.

---

## STEP 1 — Fetch Enrollments and Identify Course

**Type:** Internal — extract `inferred_course_name` from user's message first.

**[TOOL CALL]**
```
get_user_enrollments(user_id = <session_user_id>)
```

**Matching logic:**

| Condition | Action |
|-----------|--------|
| `inferred_course_name` matches exactly 1 enrollment | → STEP 2 with that enrollment |
| Partial match / multiple matches | Ask user to confirm from the shortlist |
| No match / no enrollments | Ask user to provide exact course name; re-run after response |

> **Agent note:** When presenting enrolled course list for clarification, prioritize courses with `status=1` (In Progress) as these are most relevant to a progress complaint.

---

## STEP 2 — Check Enrollment Status and Identify Incomplete Resources

From the matched enrollment record:

| Condition | Action |
|-----------|--------|
| `status = 2` (Completed, 100%) | Inform user the course is already completed. Check certificate SOP if needed. |
| `status = 1` (In Progress) | Identify resources where `resource.status ≠ 2`. Collect `resource_id` and `resource_name` of all incomplete items. → STEP 3 |

---

## STEP 3 — Identify Resource Type

**[TOOL CALL — For each incomplete resource]**
```
get_content_metadata(content_id = <resource_id>)
```

Extract: `resource_type` (SCORM, MP4, PDF, YouTube, Assessment, etc.)

**Route based on resource type:**

| Resource Type | Action |
|---------------|--------|
| `SCORM` | → STEP 4A |
| `MP4`, `PDF`, or other non-SCORM format | → STEP 4B |
| `Assessment` | → STEP 4C |

---

## STEP 4A — Incomplete Resource is SCORM Format

**User Message:**
> "Upon checking, we found that you are enrolled in **[Course Name]**. The following resource is currently in progress or not started:
>
> **Resource:** [resource_name]
> **Format:** SCORM
>
> Please note the following important points for SCORM resources:
> - This resource is designed to be completed in a **single session**.
> - **Increasing the playback speed may prevent progress from being recorded correctly**, which could affect certificate generation.
> - The resource duration is approximately **[XX minutes]** — you must spend at least **[minimum_time]** on this resource.
> - At the end of the resource, click the **"Next"** button in the upper-right corner to move to the next item. Only then will the progress update as completed.
>
> **Steps to identify and complete the pending resource:**
> 1. Go to **Profile**.
> 2. Click on **My Learning**.
> 3. Open the **In Progress** section under Contents.
> 4. Locate and select **[Course Name]**.
> 5. Click **Resume**.
> 6. Expand each module using the **(+)** icon.
> 7. Look for items **without a blue tick mark** — these are incomplete.
> 8. Complete all pending items until every module shows a blue tick.
>
> The overall course progress must reach **100%** before a certificate can be generated."

**Ticket rule:** ❌ No ticket. Close after user confirms or follow-up is not needed.

---

## STEP 4B — Incomplete Resource is Non-SCORM Format (MP4, PDF, etc.)

**User Message:**
> "Upon checking, we found that you are enrolled in **[Course Name]**. The following resource is currently in progress or not started:
>
> **Resource:** [resource_name]
>
> Kindly revisit the course and complete this resource again to update your progress.
>
> **Steps to identify and complete the pending resource:**
> 1. Go to **Profile**.
> 2. Click on **My Learning**.
> 3. Open the **In Progress** section under Contents.
> 4. Locate and select **[Course Name]**.
> 5. Click **Resume**.
> 6. Expand each module using the **(+)** icon.
> 7. Look for items **without a blue tick mark** — these are incomplete.
> 8. Complete all pending videos or content items until every module shows a blue tick.
>
> The overall course progress must reach **100%** before a certificate can be generated."

**Ticket rule:** ❌ No ticket. Close after responding.

---

## STEP 4C — Pending Item is an Assessment

First verify from the enrollment data: are all non-assessment learning resources completed?

**Sub-case A — All learning resources complete, only assessment pending:**

**User Message:**
> "Upon checking, we found that all learning resources in **[Course Name]** have been completed. However, the **assessment is still pending**.
>
> Kindly complete the assessment to achieve 100% course completion."

**If the user reports being unable to complete the assessment, check:**

| User's Situation | Action |
|-----------------|--------|
| Accessing assessment via **mobile app** | Advise user to switch to web browser (desktop/laptop). If resolved → close. |
| Receiving an **error message** | → Sub-case B |
| Seeing **"attempt limit exceeded"** message | → Sub-case B |

**Sub-case B — Assessment attempt limit exceeded or error received:**

**Instruction:**
Set `"escalate": true` in your JSON decision, and include the issue details in the escalation reason, including completion_status: "all_resources_complete_assessment_pending".

**User Message:**
> "We understand you are unable to complete the assessment. We have raised a support ticket and shared the details with the concerned team for investigation. We will update you once we have more information."

---

## SOP-C2 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| SCORM resource incomplete | ❌ |
| Non-SCORM resource incomplete | ❌ |
| Assessment pending (no error) | ❌ |
| Assessment error or attempt limit exceeded | ✅ |

---
---

# SOP-C3: Resource / Content Not Opening

## Purpose
Resolve cases where a course resource or content item fails to load or open for the user.

---

## STEP 1 — Detect Access Device / Platform

**Type:** Check session context first. If device/platform is available from session metadata, use it directly. Only ask the user if not detectable.

| Detected Platform | Action |
|-------------------|--------|
| Mobile Application | → STEP 2 |
| Web Browser (Desktop/Laptop) | → STEP 3 |
| Unknown / not detectable | Ask: "Are you accessing the platform through the mobile app or a web browser on your desktop/laptop?" |

---

## STEP 2 — User is on Mobile Application

**User Message:**
> "It appears that the resource you are trying to access may not be supported on the mobile application. Kindly try accessing the course through a desktop or laptop browser using the steps below:
>
> 1. Open **Google Chrome** on your desktop or laptop.
> 2. Go to the **iGOT Karmayogi** portal.
> 3. Log in with your registered credentials.
> 4. Search for the course you are enrolled in.
> 5. Click **Resume** and try opening the resource again."

**If the user wants to continue on mobile → STEP 2A.**
**If the issue is resolved on desktop → Close politely.**

---

## STEP 2A — User Insists on Mobile Device

**Guide Desktop Mode:**

**User Message:**
> "If you prefer to use your mobile device, kindly try the following steps to enable Desktop Mode in your mobile browser:
>
> 1. Open **Google Chrome** on your mobile device.
> 2. Tap the **three dots (⋮)** at the top-right corner.
> 3. Enable **Desktop Site / Desktop Mode**.
> 4. Go to the **iGOT Karmayogi** portal.
> 5. Log in with your registered credentials.
> 6. Search for the course and click **Resume**."

**If issue persists after desktop mode → STEP 2B.**

---

## STEP 2B — Resource Still Not Loading on Mobile

Collect from the user (if not already available in session):
- Course Name
- Resource Name
- Mobile Device Model
- Mobile Application Version

**Instruction:**
Set `"escalate": true` in your JSON decision, and include the device/app details in the escalation reason.

**User Message:**
> "We have raised a support ticket with the details provided. Our technical team will investigate and get back to you with an update."

---

## STEP 3 — User is on Web Browser → Identify Course and Resource

**[TOOL CALL]**
```
get_user_enrollments(user_id = <session_user_id>)
```

Match the course from the user's message using `inferred_course_name`. If ambiguous, present a shortlist of In Progress enrollments.

Once the course is confirmed, identify the specific resource the user cannot open. If the user cannot name the resource:
- Pull the resource list from the enrollment record.
- Present the list and ask the user to confirm which one is failing.

**[TOOL CALL — Once resource is identified]**
```
get_content_metadata(content_id = <resource_id>)
```

Extract: `resource_type`, `streaming_url`, `registration_url`, `artifact_url`.

**Route based on resource type:**

| Resource Type | Action |
|---------------|--------|
| `YouTube` | → STEP 4 |
| `SCORM`, `MP4`, `PDF`, or other | → STEP 5 |

---

## STEP 4 — YouTube Resource: Validate URL Configuration

From `get_content_metadata` response, compare:
- `streaming_url`
- `registration_url`
- `artifact_url`

| URL Comparison Result | Action |
|----------------------|--------|
| All three URLs are **identical** | Configuration is correct → STEP 4A (Check YouTube accessibility) |
| Any URL is **different** from the others | Configuration issue detected → STEP 4B (Raise ticket) |

---

## STEP 4A — URLs Match: Check YouTube Accessibility

**User Message:**
> "The resource configuration appears to be correct. Could you confirm whether YouTube is blocked or restricted on your system or network?"

| User Response | Action |
|--------------|--------|
| **Yes, YouTube is restricted** | Advise user to access via mobile app instead: *Open iGOT Karmayogi app → Log in → Search course → Resume → Watch video.* Close after guiding. |
| **No, YouTube is not restricted** | Collect Course Name and Resource Name → STEP 4B (Raise ticket) |

---

## STEP 4B — YouTube URL Mismatch or Unresolved Issue: Raise Ticket

**Instruction:**
Set `"escalate": true` in your JSON decision, and include the issue details in the escalation reason, including streaming_url, registration_url, artifact_url, and youtube_restricted.

**User Message:**
> "It appears there may be a configuration issue with this resource. We have raised a support ticket for our technical team to investigate and resolve."

---

## STEP 5 — SCORM / MP4 / PDF / Other Format: Raise Ticket Immediately

**Instruction:**
Set `"escalate": true` in your JSON decision, and include the issue details in the escalation reason.

**User Message:**
> "We have raised a support ticket for the resource loading issue. Our technical team will review and provide an update shortly."

---

## SOP-C3 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| Mobile app — switch to desktop resolves it | ❌ |
| Mobile desktop mode resolves it | ❌ |
| Mobile — issue persists after desktop mode | ✅ |
| Web — YouTube, all URLs match, not restricted, still failing | ✅ |
| Web — YouTube URL mismatch detected | ✅ |
| Web — SCORM / MP4 / PDF / other not loading | ✅ |

---
---

# SOP-C4: Learning Progress Not Reflecting in External Portals (eHRMS / Shiksha Path / SPARROW)

## Purpose
Resolve cases where a user's completed learning is not reflecting in eHRMS, Shiksha Path, or SPARROW/APAR.

---

## STEP 1 — Identify Target Portal

**Type:** Infer from user's message.

| Signal in Message | Route |
|-------------------|-------|
| "eHRMS", "HRMS" | → SECTION A |
| "Shiksha Path", "Shiksha", "CBDT" | → SECTION B |
| "SPARROW", "APAR", "CAP" | → SECTION C |
| Cannot determine | Ask: "Which portal is your learning not reflecting in — eHRMS, Shiksha Path, or SPARROW/APAR?" |

---

## SECTION A — Learning Not Reflecting in eHRMS

### STEP A1 — Fetch eHRMS Mapping Details

**[TOOL CALL]**
```
get_user_feed(user_id = <session_user_id>)
```

Extract: `eHRMS_ID`, `external_system_name`.

**Route based on field availability:**

| eHRMS ID | External System Name | Action |
|----------|---------------------|--------|
| ✅ Present | ✅ Present | → STEP A2 (Both available — redirect to eHRMS team) |
| ❌ Missing | — | → STEP A3 (eHRMS ID missing — escalate to MDO) |
| ✅ Present | ❌ Missing | → STEP A4 (External System Name missing — escalate to MDO) |

---

### STEP A2 — Both Fields Available → Redirect to eHRMS Support

**User Message:**
> "Upon checking, we found that your **eHRMS ID** and **External System Name** are both available and correctly mapped in our system.
>
> Since the required details are in place from our end, we request you to connect with the **eHRMS support team** directly for further investigation into why the progress is not reflecting."

**Ticket rule:** ❌ No ticket. Close after responding.

---

### STEP A3 — eHRMS ID Not Available → Escalate to MDO

**[TOOL CALL]**
```
get_mdo_details(org_id = <user_profile.org_id>)
```

**User Message:**
> "Upon checking, we found that your **eHRMS ID is not available** in the system.
>
> The eHRMS ID cannot be updated by individual users — it must be updated by your organization's MDO.
>
> Kindly contact the below MDO and request them to update your eHRMS ID:
>
> **Organization:** [org_name]
> **MDO Name:** [mdo_name]
> **MDO Email ID:** [mdo_email]
>
> Please note: Once the eHRMS ID is updated, it may take up to **24 hours** for your learning progress to reflect in eHRMS."

**Ticket rule:** ❌ No ticket. Close after responding.

---

### STEP A4 — External System Name Missing → Escalate to MDO

**[TOOL CALL]**
```
get_mdo_details(org_id = <user_profile.org_id>)
```

**User Message:**
> "Upon checking, we found that your eHRMS ID is available; however, the **External System Name is not updated** in the system.
>
> The External System Name is mandatory for eHRMS synchronization and can only be updated by your MDO/Administrator.
>
> Kindly contact the below MDO and request them to update this field:
>
> **Organization:** [org_name]
> **MDO Name:** [mdo_name]
> **MDO Email ID:** [mdo_email]
>
> Please note: Once updated, it may take up to **24 hours** for your learning progress to reflect in eHRMS."

**Ticket rule:** ❌ No ticket. Close after responding.

---

## SECTION B — Learning Not Reflecting in Shiksha Path

### STEP B1 — Inform User of Platform Ownership

**User Message:**
> "Shiksha Path is managed by the **Directorate of Training, CBDT**. It is not managed or operated by Karmayogi Bharat or DoPT.
>
> For any Shiksha Path-related issues, kindly coordinate directly with the Directorate of Training / CBDT team:
>
> **Support Email:** aed4.training@incometax.gov.in"

**Ticket rule:** ❌ No ticket. Close after responding.

---

## SECTION C — Learning Not Reflecting in SPARROW / APAR

### STEP C1 — Fetch User Profile

**[TOOL CALL]**
```
get_user_profile(user_id = <session_user_id>)
```

Extract: `organization`, `designation`, `group`, `profile_verification_status`.

**Route based on organization mapping:**

| Organization Mapping | Action |
|---------------------|--------|
| User mapped to **iGOT** or **Karmayogi Prarambh Trainee** | → STEP C2 (Guide transfer request) |
| User mapped to correct department | → STEP C3 (Profile verification check) |

---

### STEP C2 — User Mapped to iGOT / Karmayogi Prarambh Trainee

**User Message:**
> "APAR courses and training plans are assigned only after your profile is mapped to the correct department/organization.
>
> Since your profile is currently mapped to iGOT/Karmayogi Prarambh Trainee, kindly raise a **Transfer Request** to move your profile to the appropriate organization.
>
> Once the transfer is complete, your APAR assignments should reflect accordingly."

**Ticket rule:** ❌ No ticket. Guide user through transfer request process and close.

---

### STEP C3 — Profile Verification Check

| Profile Verification Status | Action |
|----------------------------|--------|
| ✅ Verified | → STEP C4 (Confirm profile details with user) |
| ❌ Not Verified | → STEP C7 (Profile not verified flow) |

---

### STEP C4 — Profile Verified → Confirm Details with User

**User Message:**
> "As checked, your profile is currently verified with the following details:
>
> **Organization:** [organization]
> **Designation:** [designation]
> **Group:** [group]
>
> Kindly confirm whether these details are correct."

| User Response | Action |
|--------------|--------|
| Details are **correct** | → STEP C5 |
| Details are **incorrect** | → STEP C6 |

---

### STEP C5 — Profile Correct → Validate APAR / CAP Assignment

**[TOOL CALL]**
```
get_apar_assignments(user_id = <session_user_id>)
```

Also verify enrollment and completion via:
```
get_user_enrollments(user_id = <session_user_id>)
```

**Routing:**

| APAR/CAP Assignment | Action |
|--------------------|--------|
| Not assigned — MDO exists | Share MDO details. Inform: only APAR-assigned and completed courses reflect in SPARROW. Close. |
| Not assigned — No MDO | Fetch `get_yp_am_details`. Share YP/AM contact. Close. |
| Assigned → CAP assessment not passed | Inform user to reattempt and pass the CAP assessment. Close. |
| Assigned → Completion data reflecting in APIs, within 24 hours | Inform user: data may take up to 24 hours to reflect in SPARROW. Close. |
| Assigned → Completion data reflecting, more than 24 hours passed | → STEP C5A |

---

### STEP C5A — More Than 24 Hours Passed, Data Still Not in SPARROW

Check: Does the user's SPARROW email ID match their iGOT registered email ID?

| Email Match | Action |
|-------------|--------|
| **Email IDs match** | Share SPARROW support email: `support-sparrow@gov.in`. Close. |
| **Email IDs do not match** | Share reference video: *"How to Fetch iGOT Training Data into SPARROW APAR_V8.mp4"*. Close. |

---

### STEP C6 — Profile Details Incorrect

Ask the user which detail is incorrect:

| Incorrect Field | Follow |
|----------------|--------|
| Designation | Designation Update Flow |
| Organization | Transfer Request Flow |
| Group / other profile details | Profile Update Flow |

**User Message (after routing):**
> "Kindly update the required profile details and recheck your APAR courses and training plans after the update is completed."

**Ticket rule:** ❌ No ticket. Close after guidance.

---

### STEP C7 — Profile Not Verified

Ask whether the user belongs to All India Services (IAS / IPS / IFS):

**If user belongs to All India Services:**

Verify (via profile): Cadre Details, Service Details, Batch Information, Central Deputation Details.

| Mandatory Fields | Action |
|-----------------|--------|
| All present | Profile verification should proceed — guide user through verification. |
| Some missing | Inform: mandatory service-related details are missing. Guide user to update them. APAR plans may reflect within 2–3 hours after successful update. |

**If user does NOT belong to All India Services:**

**User Message:**
> "Since your profile is currently not verified, kindly complete the **profile verification process**.
>
> Once verification is completed successfully, your APAR courses and training plans should reflect in your account."

**Ticket rule:** ❌ No ticket. Close after guidance.

---

## SOP-C4 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| eHRMS — both fields present (redirect to eHRMS team) | ❌ |
| eHRMS — ID or External System Name missing (MDO action needed) | ❌ |
| Shiksha Path — redirect to CBDT | ❌ |
| SPARROW — profile not verified | ❌ |
| SPARROW — APAR not assigned | ❌ |
| SPARROW — assessment not passed | ❌ |
| SPARROW — email mismatch or data not reflecting after 24 hours | ❌ (redirect to SPARROW support) |

---
---

# SOP-C5: Request to Unenroll from a Course / Program / Event

## Purpose
Handle user requests to unenroll from a course, program, or event they are already enrolled in.

---

## STEP 1 — Inform User About Platform Limitation

**Type:** No tool call required.

**User Message:**
> "Thank you for reaching out. Currently, the platform does not support the ability to unenroll from an already enrolled course, program, or event.
>
> You may consider either:
> - **Continuing and completing** the enrolled course/program/event, or
> - **Ignoring** the enrolled content if you do not wish to proceed with it.
>
> We apologize for any inconvenience this may cause. Please feel free to reach out if you need any further assistance."

**Ticket rule:** ❌ No ticket required. This is a platform feature limitation, not a technical issue.
**Important:** Do not promise a workaround or a timeline for when unenrollment will be available.

---

## SOP-C5 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| User requests unenrollment | ❌ — Platform limitation, inform and close |
