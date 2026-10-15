import pytest

from services.discord_moderation import DiscordModerationError, _guild_allowed, _parse_iso


def test_event_dates_are_normalized_to_utc():
    assert _parse_iso("2026-10-20T20:00:00-06:00", "start_at") == "2026-10-21T02:00:00Z"


def test_invalid_date_is_rejected():
    with pytest.raises(DiscordModerationError):
        _parse_iso("mañana", "start_at")


def test_guild_allowlist_is_enforced():
    with pytest.raises(DiscordModerationError):
        _guild_allowed(123, frozenset({456}))
