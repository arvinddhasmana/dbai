import pytest

from agent_server.config import get_settings


def test_approval_required_support_tiers_are_configurable_and_normalized(
    monkeypatch,
):
    monkeypatch.setenv("APPROVAL_REQUIRED_SUPPORT_TIERS", " Gold,  platinum ")

    settings = get_settings()

    assert settings.approval_required_support_tiers == frozenset({"gold", "platinum"})
    assert settings.requires_manager_approval("GOLD")
    assert settings.requires_manager_approval(" platinum ")
    assert not settings.requires_manager_approval("Silver")


@pytest.mark.parametrize("value", ["", "Gold,,Platinum", "Gold, "])
def test_invalid_approval_required_support_tiers_fail_explicitly(monkeypatch, value):
    monkeypatch.setenv("APPROVAL_REQUIRED_SUPPORT_TIERS", value)

    with pytest.raises(ValueError, match="APPROVAL_REQUIRED_SUPPORT_TIERS"):
        get_settings()
