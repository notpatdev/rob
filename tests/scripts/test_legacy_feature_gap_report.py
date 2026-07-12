from __future__ import annotations

from pathlib import Path


def test_legacy_feature_gap_report_exists_with_required_sections():
    report_path = Path("docs/legacy-feature-gap-report.md")
    assert report_path.exists()

    content = report_path.read_text(encoding="utf-8")
    assert "| Feature | Old Rob behaviour | v2 status | Should port? | Priority | Notes |" in content
    for required in (
        "Registration",
        "Throne webhook tracking",
        "Public send notifications",
        "Leaderboards",
        "Counting",
        "Send requests",
        "Manual sends",
        "Maintenance",
        "robctl",
        "Rules",
        "Reports",
        "DM audit",
        "Carl-bot warn relay",
        "Blacklist",
        "Inactivity removal",
        "Moderation helpers",
        "Event runtime",
        "UI/card system",
    ):
        assert required in content
