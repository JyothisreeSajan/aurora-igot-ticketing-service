"""
Unit tests for app/core/utils/mdo_lookup.py

Covers the MDO_LEADER-preferred / MDO_ADMIN-fallback contact resolution
helper shared by get_mdo_details, get_mdo_details_by_org_id,
get_org_admin_details, and the SOP-A3 get_department_mdo_admin tool.
"""

from unittest.mock import MagicMock, patch

from app.core.utils.mdo_lookup import (
    find_mdo_contact,
    find_mdo_contact_by_channel,
    pick_mdo_entry,
)


def _entry(roles, name="Some Admin"):
    return {
        "organisations": [{"roles": roles}],
        "profileDetails": {"personalDetails": {"firstname": name}},
    }


def _mock_response(content):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"result": {"response": {"content": content}}}
    return resp


class TestPickMdoEntry:
    def test_prefers_mdo_leader_over_mdo_admin(self):
        leader = _entry(["MDO_LEADER"], "Leader")
        admin = _entry(["MDO_ADMIN"], "Admin")
        entry, role = pick_mdo_entry([admin, leader])
        assert entry is leader
        assert role == "MDO_LEADER"

    def test_falls_back_to_mdo_admin_when_no_leader(self):
        admin = _entry(["MDO_ADMIN"], "Admin")
        entry, role = pick_mdo_entry([admin])
        assert entry is admin
        assert role == "MDO_ADMIN"

    def test_returns_none_when_neither_role_present(self):
        other = _entry(["SOME_OTHER_ROLE"])
        entry, role = pick_mdo_entry([other])
        assert entry is None
        assert role is None

    def test_empty_or_missing_content_list(self):
        assert pick_mdo_entry([]) == (None, None)
        assert pick_mdo_entry(None) == (None, None)

    def test_entry_with_multiple_orgs_and_missing_roles_key(self):
        entry_with_gap = {
            "organisations": [{"roles": None}, {"roles": ["MDO_LEADER"]}],
        }
        entry, role = pick_mdo_entry([entry_with_gap])
        assert entry is entry_with_gap
        assert role == "MDO_LEADER"


class TestFindMdoContact:
    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_single_call_when_leader_found_first(self, mock_post):
        leader = _entry(["MDO_LEADER"])
        mock_post.return_value = _mock_response([leader])

        entry, role, count = find_mdo_contact("http://x", {}, "org-1")

        assert entry is leader
        assert role == "MDO_LEADER"
        assert count == 1
        mock_post.assert_called_once()
        sent_payload = mock_post.call_args.kwargs["json"]
        assert sent_payload["request"]["filters"]["organisations.roles"] == ["MDO_LEADER"]
        assert sent_payload["request"]["filters"]["rootOrgId"] == "org-1"

    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_falls_back_to_second_call_for_admin(self, mock_post):
        admin = _entry(["MDO_ADMIN"])
        # First call (MDO_LEADER) returns nothing, second call (MDO_ADMIN) returns admin.
        mock_post.side_effect = [_mock_response([]), _mock_response([admin])]

        entry, role, count = find_mdo_contact("http://x", {}, "org-1")

        assert entry is admin
        assert role == "MDO_ADMIN"
        assert count == 1
        assert mock_post.call_count == 2
        second_payload = mock_post.call_args_list[1].kwargs["json"]
        assert second_payload["request"]["filters"]["organisations.roles"] == ["MDO_ADMIN"]

    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_no_contact_found_after_both_calls(self, mock_post):
        mock_post.side_effect = [_mock_response([]), _mock_response([])]

        entry, role, count = find_mdo_contact("http://x", {}, "org-1")

        assert (entry, role, count) == (None, None, 0)
        assert mock_post.call_count == 2

    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_find_mdo_contact_by_channel_filters_by_channel(self, mock_post):
        leader = _entry(["MDO_LEADER"])
        mock_post.return_value = _mock_response([leader])

        entry, role, count = find_mdo_contact_by_channel("http://x", {}, "some-department")

        assert entry is leader
        assert role == "MDO_LEADER"
        sent_payload = mock_post.call_args.kwargs["json"]
        assert sent_payload["request"]["filters"]["channel"] == "some-department"
        assert "rootOrgId" not in sent_payload["request"]["filters"]
