# Agent SOP — Login & Account Issues Resolution
**Platform:** iGOT Karmayogi | **Agent Framework:** LangGraph
**Applies to:** L1 AI Agent — Login, Account, and Access Use Cases

---

## Global Agent Principles
- **Tool-first, ask-last.** Fetch all available account and profile data via API before prompting the user for any information the system already holds.
- **Infer from context.** The user's session provides their current registered details (user ID, email, mobile, organization). Use these directly in all API calls without asking the user to confirm what the system already knows.
- **Pre-fetch before presenting choices.** When a decision point requires account impact data (enrollments, org info, transfer status), retrieve it first and include it in the same message that presents the choice — never ask the user to decide before they have the facts.
- **Ticket only with explicit user confirmation.** For account changes, a ticket is raised only after the user has reviewed all impact details and given final explicit confirmation.

---

## Tool Registry

| Tool | Signature | Purpose |
|------|-----------|---------|
| `get_user_profile` | `(user_id)` | Fetch user profile: org, designation, group, verification status, ministry/state |
| `validate_email_domain` | `(email)` | Check whether the email domain is whitelisted on the platform |
| `lookup_user_by_contact` | `(email_or_mobile)` | Check whether an email or mobile number is already registered; returns account details if found |
| `get_user_enrollments` | `(user_id)` | Fetch enrollment count, in-progress count, and completed count for a user |
| `get_user_transfer_request` | `(user_id)` | Read the `wfTransferRequest` field from User Read API to check transfer request status |
| `get_mdo_details` | `(org_id)` | Fetch MDO Leader name and email ID for a given organization |
| `get_yp_am_details` | `(ministry_or_state)` | Fetch YP/AM name, email, and contact details |

---
---

# SOP-L1: Email / Mobile Number Update & Multiple Account Handling

## Purpose
Handle cases where a user is trying to update their registered Email ID or Mobile Number, encounters an error during the update, or attempts to use a contact detail that is already linked to another account.

---

## STEP 1 — Identify What the User Wants to Update

**Type:** Infer from user's message — no tool call yet.

| Signal in Message | Update Type |
|-------------------|-------------|
| Mentions email address or "email" | `email_update` |
| Mentions phone, mobile, or number | `mobile_update` |
| Unclear | Ask: "Kindly share the new Email ID or Mobile Number you would like to update your profile with." |

**Note:** The user's **current** registered details are already available from session context. Only the **new** email/mobile they want to switch to needs to be collected.

---

## STEP 2 — Collect New Email / Mobile from User

Ask the user:
> "Kindly share the new [Email ID / Mobile Number] you would like to update on your profile."

Once received, store as `new_contact`.

**Route based on update type:**

| Update Type | Next Step |
|-------------|-----------|
| `email_update` | → STEP 3 (Validate domain) |
| `mobile_update` | → STEP 4 (Skip domain check; go directly to registration check) |

---

## STEP 3 — Validate Email Domain

**[TOOL CALL]**
```
validate_email_domain(email = <new_contact>)
```

| Domain Validation Result | Action |
|--------------------------|--------|
| Domain is **valid / whitelisted** | → STEP 4 |
| Domain is **NOT valid / not whitelisted** | → STEP 3A |

---

## STEP 3A — Domain Not Whitelisted → Share MDO / YP Contact

**[TOOL CALL]**
```
get_user_profile(user_id = <session_user_id>)
get_mdo_details(org_id = <user_profile.org_id>)
```

If MDO details are not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

**User Message:**
> "Upon checking, we found that the email domain of the address you provided is not whitelisted on the platform, and therefore cannot be used for account updates.
>
> Kindly connect with the concerned authority below for clarification on approved email domains for your organization:
>
> **Name:** [mdo_or_yp_name]
> **Email ID:** [mdo_or_yp_email]"

**Ticket rule:** ❌ No ticket. Close politely.

---

## STEP 4 — Check Whether Email / Mobile is Already Registered

**[TOOL CALL]**
```
lookup_user_by_contact(email_or_mobile = <new_contact>)
```

| Result | Action |
|--------|--------|
| **Not registered** on the platform | → STEP 5 (Guide profile update) |
| **Already registered** to another account | → STEP 6 (Registered account flow) |

---

## STEP 5 — Not Registered → Guide Profile Update

**User Message:**
> "The [Email ID / Mobile Number] you provided is available and not linked to any other account.
>
> Kindly follow the steps below to update it from your profile:
>
> 1. Go to **View Profile**.
> 2. Navigate to **Other Details**.
> 3. Click the **Edit (Pen) Icon** next to the Email ID / Mobile Number field.
> 4. Enter your new [Email ID / Mobile Number].
> 5. Click **Request OTP**.
> 6. Enter the OTP received.
> 7. Click **Save Changes**.
>
> Your [Email ID / Mobile Number] will be updated successfully after OTP verification."

**If user reports not receiving the OTP → STEP 5A.**
**If update is successful → Close politely.**

---

## STEP 5A — OTP Not Received → Share MDO / YP Contact

**[TOOL CALL]**
```
get_mdo_details(org_id = <user_profile.org_id>)
```

If MDO details are not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

**User Message:**
> "If you are not receiving the OTP, kindly connect with the below contact for further assistance:
>
> **Name:** [mdo_or_yp_name]
> **Email ID:** [mdo_or_yp_email]"

**Agent note:** Do not attempt to generate or send OTPs from the support workflow. OTP generation and verification must be completed by the user through the platform's profile update process.

**Ticket rule:** ❌ No ticket. Close politely.

---

## STEP 6 — Already Registered → Fetch Both Account Summaries

The provided contact belongs to another existing account. Before presenting any decision to the user, fetch the full context for both accounts.

**[TOOL CALL — Run in parallel]**
```
// Existing account linked to the new contact
lookup_user_by_contact(email_or_mobile = <new_contact>)
  → returns: other_user_id, other_org_name

get_user_enrollments(user_id = <other_user_id>)
  → returns: other_total_enrollments, other_in_progress, other_completed

// Current session account
get_user_enrollments(user_id = <session_user_id>)
  → returns: current_total_enrollments, current_in_progress, current_completed
```

**User Message:**
> "Upon checking, we found that the [Email ID / Mobile Number] **[new_contact]** is already registered and linked to an existing account with the following details:
>
> **Existing Account:**
> Organization: [other_org_name]
> Total Enrollments: [other_total_enrollments]
> In Progress: [other_in_progress]
> Completed: [other_completed]
>
> **Your Current Account:**
> Total Enrollments: [current_total_enrollments]
> In Progress: [current_in_progress]
> Completed: [current_completed]
>
> How would you like to proceed?"

**Route based on user response:**

| User Response | Action |
|--------------|--------|
| Does **not** want to proceed | → STEP 6A |
| Requests **account merge** | → STEP 6B |
| Confirms they want to **proceed with the update** | → STEP 6C |

---

## STEP 6A — User Does Not Want to Proceed

**User Message:**
> "Understood. No changes have been made to your account. Please feel free to reach out if you need any further assistance."

**Ticket rule:** ❌ No ticket. Close politely.

---

## STEP 6B — User Requests Account Merge

**User Message:**
> "We understand your request; however, account merging is currently not supported on the platform.
>
> If you would like to explore other options, please feel free to reach out to us."

**Ticket rule:** ❌ No ticket. Close politely.

---

## STEP 6C — User Wants to Proceed → Confirm Impact and Get Final Confirmation

**User Message:**
> "Please review the following details carefully before confirming:
>
> **Current Email ID / Mobile:** [current_contact]
> **New Email ID / Mobile to be Updated:** [new_contact]
>
> **Important — Impact of this change:**
> - The existing account currently associated with **[new_contact]** will be **deactivated**.
> - All access to that account will be **permanently removed**.
> - That account currently has [other_total_enrollments] enrollment(s), [other_in_progress] in progress, and [other_completed] completed.
>
> Do you confirm that you would like to proceed with this update?"

| User Final Response | Action |
|--------------------|--------|
| **Yes, confirmed** | → STEP 6D (Raise support ticket) |
| **No, do not proceed** | Restart from STEP 2. Ask the user to share the correct email/mobile they wish to update and begin validation again. |

---

## STEP 6D — User Confirmed → Escalate

**Instruction:**
Do not call any tool here. Set `"escalate": true` in your final JSON decision to route the issue to a human agent, providing the confirmation details as the reason.

**User Message:**
> "Thank you for confirming. Your request has been recorded and escalated to the concerned team for further processing. You will receive an update once the change has been completed."

**Ticket rule:** ✅ Escalate only after explicit final confirmation in STEP 6D.

---

## SOP-L1 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| Domain not whitelisted | ❌ |
| Email/mobile not registered — guided to update via profile | ❌ |
| OTP not received — MDO/YP shared | ❌ |
| Already registered — user declines to proceed | ❌ |
| Already registered — user requests account merge | ❌ |
| Already registered — user confirms update after impact review | ✅ |

---
---

# SOP-L2: Access Revoked

## Purpose
Handle cases where a user's access has been revoked and they see the error:
> *"Your access has been revoked because your organization no longer identifies you as a user. To regain access, please submit a 'Transfer Request' and choose the correct organization."*

---

## STEP 1 — Fetch Profile and Transfer Request Status Immediately

**Type:** Do not ask the user for any information yet. Run API calls as soon as the issue is identified.

**[TOOL CALL — Run in parallel]**
```
get_user_profile(user_id = <session_user_id>)
  → returns: organization, profile_status, ministry, state, department

get_user_transfer_request(user_id = <session_user_id>)
  → returns: wfTransferRequest field value
```

**Expected profile state in access-revoked cases:**
- Organization is typically mapped as **"iGOT"** by default
- Profile status typically appears as **"Not My User"**

**Route based on `wfTransferRequest` field value:**

| `wfTransferRequest` Value | Meaning | Action |
|---------------------------|---------|--------|
| `{ "Org Name": "<organization>" }` — contains an org name | Transfer request already raised | → STEP 2 |
| `{}` — empty object | No transfer request raised | → STEP 3 |

---

## STEP 2 — Transfer Request Already Raised → Confirm Organization with User

**User Message:**
> "Upon reviewing your account, we found that a **Transfer Request has already been raised** to the following organization:
>
> **Organization:** [wfTransferRequest.org_name]
>
> Could you please confirm whether this is the correct organization you wish to be transferred to?"

| User Response | Action |
|--------------|--------|
| **Yes, correct organization** | → STEP 2A (Fetch MDO and share for approval) |
| **No, incorrect organization** | → STEP 2B (Guide to cancel and re-raise) |

---

## STEP 2A — Organization Confirmed → Fetch MDO and Guide Approval

**[TOOL CALL]**
```
get_mdo_details(org_id = <wfTransferRequest.org_id>)
```

If MDO details are not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

**If MDO exists:**

**User Message:**
> "Your transfer request to **[org_name]** is pending approval from the MDO Admin/Leader of that organization.
>
> Kindly connect with the MDO Admin below and request them to review and approve your transfer request:
>
> **MDO Admin Name:** [mdo_name]
> **MDO Admin Email ID:** [mdo_email]
>
> Once approved, your access will be restored."

**If no MDO exists — share YP/SPOC instead:**

**User Message:**
> "Your transfer request to **[org_name]** is pending; however, there is currently no MDO Admin/Leader configured for that organization.
>
> Kindly connect with the SPOC below to coordinate the creation of an MDO Admin or to get approval support:
>
> **YP/AM Name:** [yp_name]
> **Email ID:** [yp_email]
> **Contact Number:** [yp_contact]"

**Ticket rule:** ❌ No ticket. Close after sharing contact details.

---

## STEP 2B — Incorrect Organization → Guide to Cancel and Re-raise

**User Message:**
> "If the organization shown is incorrect, kindly cancel the existing transfer request and raise a fresh one with the correct organization by following these steps:
>
> **To cancel the existing request:**
> 1. Go to **Profile** (top-right corner).
> 2. Click on **View Profile**.
> 3. Select **Make Transfer Request**.
> 4. Click **Cancel Transfer Request**.
>
> **To raise a fresh request:**
> Follow the steps in STEP 3 below to submit a new request with the correct organization."

Then proceed to guide the user through STEP 3.

---

## STEP 3 — No Transfer Request Raised → Guide User to Raise One

**User Message:**
> "Your previous department MDO has marked your profile as **'Not My User'**, which has revoked your access to the platform.
>
> To regain access, you need to raise a **Transfer Request** to move your profile to the correct organization. Please follow the steps below:
>
> **Step 1 — Open Transfer Request:**
> 1. Click on the **Profile Icon** (top-right corner).
> 2. Click on **View Profile**.
> 3. Select **Make Transfer Request**.
>
> **Step 2 — Fill Transfer Details:**
> 1. Select the correct **Organization Name** from the dropdown.
> 2. Choose your **Group** and **Designation** (if applicable).
> 3. Ensure all details are accurate before submitting.
>
> **Step 3 — Submit:**
> 1. Click **Submit**.
> 2. Your request will be sent to the concerned MDO Admin for approval.
>
> **Step 4 — Await Approval:**
> - The MDO Admin will review and approve your request.
> - Your access will be restored once the request is approved.
>
> *(If you submitted the request with incorrect details and it has not yet been approved, click **Cancel Transfer Request** and raise a fresh request with the correct information.)*"

**Then check for edge cases — route if user reports any of the following:**

| User Reports | Action |
|-------------|--------|
| Transfer Request button is **disabled or not clickable** | → STEP 4 |
| **Cannot find their organization** in the dropdown | → STEP 5 |

---

## STEP 4 — Transfer Request Button Disabled / Not Clickable

**Context:** This typically occurs when the user has a pending request under the Primary Details section.

**User Message:**
> "The 'Make Transfer Request' option may be disabled because you have a **pending request under Primary Details**.
>
> Kindly follow the steps below to resolve this:
>
> 1. Go to your **Profile** section.
> 2. Open the **Primary Details** section.
> 3. Click on **Withdraw Request** to cancel the pending request.
> 4. Once withdrawn, the **Make Transfer Request** option will become active.
> 5. Raise your transfer request with the correct organization details.
>
> Please let us know if the button remains disabled after withdrawing the pending request."

**Ticket rule:** ❌ No ticket initially. If issue persists after withdrawal, escalate the ticket (set `"escalate": true`).

---

## STEP 5 — Organization Not Found in Dropdown

**Collect from user:**
- Email ID or Mobile Number of the user (or a colleague from the same organization)

**[TOOL CALL]**
```
lookup_user_by_contact(email_or_mobile = <contact_provided_by_user>)
```

| Search Result | Action |
|--------------|--------|
| **User/Organization found** | → STEP 5A (Share exact organization name) |
| **User/Organization not found** | → STEP 5B (Org not found / not onboarded — share YP details) |

---

## STEP 5A — Organization Found → Share Exact Name

**User Message:**
> "We found the organization details in the system. Kindly use the exact name below when selecting your organization in the Transfer Request dropdown:
>
> **Organization Name:** [details.rootOrgName]
>
> Please try raising your transfer request again using this name."

**Ticket rule:** ❌ No ticket. Close after sharing.

---

## STEP 5B — Organization Not Found / Not Onboarded → Share YP Details

**[TOOL CALL]**
```
get_yp_am_details(ministry_or_state = <user_provided_ministry_or_state>)
```

**User Message:**
> "We were unable to find the organization in the system. This may mean the organization has not yet been onboarded onto the platform.
>
> Kindly connect with the YP/SPOC below to coordinate the onboarding or creation of your organization:
>
> **YP/AM Name:** [yp_name]
> **Email ID:** [yp_email]
> **Contact Number:** [yp_contact]"

**Ticket rule:** ❌ No ticket. Close after sharing YP contact.

---

## SOP-L2 Ticket Rules — Quick Reference

| Scenario | Raise Ticket? |
|----------|:-------------:|
| Transfer request already raised — MDO shared for approval | ❌ |
| Transfer request already raised — no MDO, YP shared | ❌ |
| No transfer request — guided to raise one | ❌ |
| Transfer button disabled — guided to withdraw pending request | ❌ |
| Organization found in backend — exact name shared | ❌ |
| Organization not found — YP shared for onboarding | ❌ |
| Transfer button still disabled after withdrawal | ✅ Escalate |
