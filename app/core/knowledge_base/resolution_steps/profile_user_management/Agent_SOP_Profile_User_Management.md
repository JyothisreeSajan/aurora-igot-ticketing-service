# Agent SOP — Profile & User Management Issues Resolution
**Platform:** iGOT Karmayogi | **Agent Framework:** LangGraph
**Applies to:** L1 AI Agent — Access Revoked, Email/Mobile Registration, Profile
Verification, Designation/Group, and Profile Update Use Cases

---

## Global Agent Principles
- Tool-first, ask-last. Fetch relevant status via API immediately before asking the user
  anything.
- Single-pass diagnosis. Fetch all relevant data upfront and deliver one complete,
  informed response.
- Be empathetic, concise, and professional in every response.

---

## Tool Registry

| Tool | Signature | Purpose |
|------|-----------|---------|
| `get_user_transfer_request_details` | `(email)` | Check whether a Transfer Request has already been raised, via `wfTransferRequest` — also the greeting-name source (SOP-A1 STEP 1) |
| `get_mdo_details_by_org_id` | `(org_id)` | Fetch MDO Admin contact for a SPECIFIC organisation id — unlike `get_mdo_details`, which always derives the org from the user's own profile (SOP-A1 STEP 2) |
| `get_yp_am_details` | `(ministry_or_state)` | YP/SPOC fallback when no MDO Admin exists — reused from login_issue_tool.py (SOP-A1 STEP 3, Edge Case 2, SOP-A2 fallback) |
| `search_organization` | `(org_name)` | Search for an organization by EXACT name (case-insensitive, no partial matching) via the Org Search API (SOP-A1 Edge Case 2) |
| `search_organization_under_ministry_or_state` | `(ministry_or_state_name, org_name)` | Second-attempt search when the exact name fails: matches the Ministry/State by name, then searches (partial match) for the org under it via the Org Hierarchy Search API (SOP-A1 Edge Case 2) |
| `get_user_profile` | `(email)` | Ticket owner's OWN organization/ministry details — reused from profile_update_tool.py (SOP-A2 STEP 2A/3.1.1, YP fallback input) |
| `get_mdo_details` | `(email)` | MDO Admin for the ticket owner's OWN organization — reused from login_issue_tool.py (SOP-A2 STEP 2A/3.1.1) |
| `validate_new_contact_domain` | `(new_email)` | Domain-whitelist check for the NEW email the user wants to update to — NOT the ticket owner's own email (SOP-A2 STEP 2) |
| `check_contact_registered` | `(new_contact)` | Checks whether the new Email ID / Mobile Number is already registered to another account; auto-detects email vs. mobile (SOP-A2 STEP 3) |
| `get_enrollment_summary` | `(user_id)` | Enrollment counts (In-Progress/Completed) for the OTHER account already linked to the new contact — internal escalation-note use only (SOP-A2 STEP 4.1) |

---
---

# SOP-A1: Access Revoked

Both Access Revoked scenarios are implemented — Transfer Request already raised, and no
Transfer Request raised yet. SOP-A2 (Email/Mobile already registered, below) is also
implemented. Every other subcategory in this category (Profile Verification/Verified Badge,
Designation/Group Not verified, Profile Update) still escalates immediately as out of scope.

Covers users who see: "Your access has been revoked because your organization no longer
identifies you as a user..." — typically because their organization is mapped as the
"iGOT" placeholder and their profile status is "Not My User".

**STEP 1.** `get_user_transfer_request_details(email)` — checks `wfTransferRequest`; also
the greeting-name source (its `firstName` field, same call).

| has_transfer_request | Action |
|---|---|
| false | → STEP 1A. Resolved. Close — formally tell the user their previous department's MDO marked their profile "Not My User", resulting in revocation of access, so they must raise a Transfer Request. As its own separate paragraph, guide them through it (Profile Icon → View Profile → Make Transfer Request → select Organization/Group/Designation → Submit → await MDO Admin approval). |
| true | → STEP 2. Tell the user a Transfer Request has already been raised to `transfer_org_name`. |

**STEP 2.** `get_mdo_details_by_org_id(org_id=transfer_root_org_id)` — MDO Admin for the
TARGET transfer organization, never the user's own (revoked) organization.

- MDO found → Resolved. Close — share MDO Admin contact (Name, Email, Mobile — masked
  placeholder tokens copied exactly); ask the user to connect with them and request
  approval.
- MDO not found → STEP 3.

**STEP 3.** `get_yp_am_details(ministry_or_state=transfer_org_name)` — YP/SPOC fallback.

- YP/SPOC found → Resolved. Close — share contact; ask the user to coordinate for
  creation of an MDO Leader/Admin and approval-related support.
- YP/SPOC not found → **escalate=true** — keep the STEP 1 context sentence (Transfer Request already raised to the target org), then briefly note the MDO/YP contact couldn't be found and that support will reach out soon. Brief, but not stripped down to one bare sentence — no "a human agent will review your case" padding.

**Before STEP 1:** if the ticket message describes the "Make Transfer Request" button/option
itself as disabled, greyed out, or not clickable (rather than a generic "access has been
revoked" report), skip STEP 1 and go directly to Edge Case 1.

## Edge Case 1 — Transfer Request Button Disabled / Not Clickable

`get_user_transfer_request_details(email)` called only for its `firstName` field (greeting
name) — `wfTransferRequest` is not relevant here. Likely caused by an existing pending
request under Primary Details blocking a fresh transfer request. Resolved. Close —
formally tell the user they currently have a pending request under Primary Details
preventing a new Transfer Request; request that they withdraw it first (once withdrawn,
"Make Transfer Request" becomes enabled). As its own separate paragraph, guide them:
Profile section → Primary Details section → Withdraw Request → submit a fresh Transfer
Request.

## Edge Case 2 — User Unable to Find Organization in Dropdown

Triggered ONLY when the user names a specific organization they cannot find — the org name
must be present in the message itself for this edge case to apply; if not, fall through to
STEP 1. Extract it directly, do not ask the user to restate it.

`get_user_transfer_request_details(email)` — greeting name only. `search_organization(org_name)`
— EXACT name match only (case-insensitive, no partial matching), pass the name exactly as
the user wrote it.

- found=true → Resolved. Close — full, formal, official-email response (not a single terse
  line): acknowledge the backend verification carried out, confirm the exact organization
  name was located, request the user select it from the dropdown, close courteously.
- found=false, AND the message also names a Ministry/State → `search_organization_under_ministry_or_state(ministry_or_state_name, org_name)`
  — matches the Ministry/State by name against the hierarchy list APIs, then searches
  (partial match) for the org under it via the Org Hierarchy Search API. found=true →
  Resolved. Close — same formal, full official-email style as above.
- found=false (either search, or no Ministry/State was named at all) → `get_yp_am_details(ministry_or_state=<Ministry/State/Department from the
  message, else the org name>)`. YP/SPOC found → Resolved. Close — tell the user we could
  not find that organization; share the YP/SPOC contact; ask them to coordinate for
  organization creation/onboarding. YP/SPOC not found → **escalate=true** — tell the user
  we could not find the organization, and their issue has been logged and escalated to the
  support team (same standard phrasing used elsewhere, not invented wording).

## SOP-A1 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| No Transfer Request raised yet | ❌ |
| Edge Case 1 — Transfer Request button disabled | ❌ |
| Edge Case 2 — org named, found | ❌ |
| Edge Case 2 — org named, not found, YP/SPOC found | ❌ |
| Edge Case 2 — org named, not found, neither found | ✅ |
| Transfer Request raised, MDO Admin found | ❌ |
| Transfer Request raised, no MDO, YP/SPOC found | ❌ |
| Transfer Request raised, neither MDO nor YP/SPOC | ✅ |

---
---

# SOP-A2: Email / Mobile Already Registered

Covers users trying to update the Email ID or Mobile Number on their profile — asking how,
reporting an error during the update, or reporting the new contact is "already registered".

**Before STEP 1.** If the message reports not receiving an OTP during an update attempt
(and isn't a fresh "how do I update" question), skip directly to STEP 3.1.1.

**STEP 1.** Identify the new Email ID / Mobile Number from the message.
  Not present → ask the user to share it (no ticket, wait for reply).
  Present, is an email → STEP 2. Is a mobile number → STEP 3 (no domain to check).

**STEP 2.** `validate_new_contact_domain(new_email)` — domain whitelist check on the NEW
contact (never the ticket owner's own, already-whitelisted, email).
  Whitelisted → STEP 3. Not whitelisted → STEP 2A.

**STEP 2A — Domain Not Whitelisted.** `get_user_profile(email=<owner>)` for the owner's own
org/ministry, then `get_mdo_details(email=<owner>)` for their MDO Admin.
  MDO found → Resolved. Close — share MDO contact, explain the domain isn't whitelisted.
  MDO not found → `get_yp_am_details(ministry_or_state=<owner's org/ministry>)`.
    YP/SPOC found → Resolved. Close — share YP/SPOC contact.
    YP/SPOC not found → **escalate=true**.

**STEP 3.** `check_contact_registered(new_contact)` — auto-detects email vs. mobile
(mobile is matched via the private User Search API's `phone` filter, plain 10-digit number,
no country code).
  Not registered → STEP 3.1. Already registered → STEP 3.2 (confirm it's genuinely a
  different account, not the ticket owner's own).

**STEP 3.1 — Not Registered.** Resolved. Close — confirm the contact is available; guide the
user through the profile update (View Profile → Other Details → Edit icon → enter new
contact → Request OTP → verify OTP → Save Changes).

**STEP 3.1.1 — OTP Not Received.** Never generate/verify an OTP directly. Same MDO → YP/SPOC
lookup pattern as STEP 2A, for the ticket owner's own organization.
  MDO or YP/SPOC found → Resolved. Close — share contact.
  Neither found → **escalate=true**.

**STEP 3.2 — Confirm the Match Isn't the Ticket Owner's Own Account.**
`check_contact_registered` matches ANY account already using that contact — including the
ticket owner's own, if they simply re-sent their current Email ID / Mobile Number unchanged.
`get_user_profile(email=<owner>)` (reuse if already called this turn) for the owner's own
user id.
  matched_user_id == owner's own id → STEP 3.3 (their own account — not a duplicate).
  matched_user_id != owner's own id → STEP 4 (genuinely a different account).

**STEP 3.3 — Contact Is Already the Owner's Own.** Resolved. Close — no ticket. Tell the
user the Email ID / Mobile Number they provided is already the one on their own account, so
no update is needed; ask them to share a different one if they meant to update to something
else.

**STEP 4 — Registered to a Different Account.** `get_enrollment_summary(user_id=<matched_user_id>)`
for the OTHER account's enrollment counts — for the internal escalation note only, never
shared with the end user. First reply: explain the contact is linked to another account, that
proceeding will deactivate that account while the user's own learning records stay put,
restate current vs. new contact, and ask for explicit confirmation. Stop and wait for the
reply.

**STEP 4 (continuation).**
  Affirmative → STEP 4.4.
  Negative → Resolved. Close — no changes made, no ticket.
  Ambiguous → ask again for a clear Yes/No (no ticket yet).

**STEP 4.4 — Confirmed.** `escalate=true`. Escalation note must include: owner's user id and
current email, the new contact requested, confirmation received, the other account's org and
enrollment counts, and confirmation the user understands the other account will be
deactivated. Tell the user the request has been recorded and shared with the team.

## SOP-A2 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| No new contact given yet | ❌ (ask for it) |
| Domain not whitelisted, MDO or YP/SPOC found | ❌ |
| Domain not whitelisted, neither found | ✅ |
| Not registered | ❌ |
| Not registered, OTP not received, MDO or YP/SPOC found | ❌ |
| Not registered, OTP not received, neither found | ✅ |
| Registered, matched account is the ticket owner's own | ❌ |
| Registered to a different account, user declines | ❌ |
| Registered to a different account, user confirms | ✅ |