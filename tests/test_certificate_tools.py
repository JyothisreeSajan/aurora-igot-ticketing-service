"""
Unit tests for app/core/tools/certificate_tools.py's get_user_details —
used by ContentRelatedSubgraph's "Certificate Issue" (Incorrect Name on
Certificate) flow as the authoritative source of the name that appears on
generated certificates.
"""

import json
from unittest.mock import MagicMock, patch

from app.core.tools.certificate_tools import get_user_details


def _mock_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _search_response(content):
    return _mock_response({"result": {"response": {"content": content}}})


class TestGetUserDetails:
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_found_returns_filtered_fields(self, mock_post):
        user = {
            "rootOrgName": "Ministry of Power",
            "channel": "Ministry of Power",
            "id": "user-1",
            "rootOrgId": "root-1",
            "firstName": "Bharath",
            "lastName": "Kumar",
            "profileDetails": {
                "professionalDetails": [
                    {"verifiedKarmayogi": True, "profileStatus": "VERIFIED", "designation": "Deputy Adviser"}
                ]
            },
            "userType": "OFFICIAL",
            "status": 1,
            "roles": ["PUBLIC"],
            "phoneVerified": True,
            "userName": "bharath_k",
            "emailVerified": True,
        }
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_details.func("bharath@x.com"))

        assert result["firstName"] == "Bharath"
        assert result["lastName"] == "Kumar"
        assert result["rootOrgName"] == "Ministry of Power"
        assert result["profileDetails"]["professionalDetails"][0]["designation"] == "Deputy Adviser"
        assert "error" not in result

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_missing_professional_details_defaults_gracefully(self, mock_post):
        user = {"firstName": "Ravi", "lastName": None, "profileDetails": {}}
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_details.func("ravi@x.com"))

        assert result["firstName"] == "Ravi"
        assert result["lastName"] is None
        assert result["profileDetails"]["professionalDetails"][0]["designation"] is None

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_not_found_returns_error(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_details.func("nobody@x.com"))

        assert result["error"] == "User details not found."

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_details.func("err@x.com"))

        assert "error" in result
        assert "firstName" not in result
