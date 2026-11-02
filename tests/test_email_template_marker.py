"""The Aurora draft marker must be present in every templated email."""
from app.core.utils.constants import AURORA_DRAFT_MARKER, build_email_html


def test_marker_present_and_hidden():
    html = build_email_html("<p>Steps</p>", name="Asha")
    assert AURORA_DRAFT_MARKER in html
    assert "display:none" in html
    assert "Hi Asha," in html and "<p>Steps</p>" in html


def test_already_wrapped_html_is_untouched():
    raw = "<html><body>x</body></html>"
    assert build_email_html(raw) == raw
