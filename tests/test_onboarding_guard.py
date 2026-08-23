"""Tests for the new-group onboarding strict-mode guard."""

from __future__ import annotations

from types import SimpleNamespace

from onboarding_guard import OnboardingGuard


def _event(origin="group:1", group_id="1"):
    return SimpleNamespace(unified_msg_origin=origin, group_id=group_id)


def _config(**overrides):
    config = {
        "onboarding_guard_minutes": 30.0,
        "onboarding_guard_messages": 20,
    }
    config.update(overrides)
    return lambda key, default: config.get(key, default)


def test_first_group_message_touches_onboarding():
    guard = OnboardingGuard(get_config=_config())

    assert guard.touch(_event()) is True


def test_non_group_event_never_onboards():
    guard = OnboardingGuard(get_config=_config())
    event = SimpleNamespace(unified_msg_origin="friend:1")

    assert guard.touch(event) is False
    assert guard.is_active(event) is False


def test_message_limit_expires_onboarding():
    guard = OnboardingGuard(
        get_config=_config(
            onboarding_guard_minutes=0.0,
            onboarding_guard_messages=2,
        )
    )
    event = _event()

    assert guard.touch(event) is True
    assert guard.touch(event) is True
    assert guard.touch(event) is False


def test_duration_expires_onboarding_with_fake_clock():
    now = [100.0]
    guard = OnboardingGuard(
        get_config=_config(
            onboarding_guard_minutes=1.0,
            onboarding_guard_messages=0,
        ),
        now=lambda: now[0],
    )
    event = _event()

    assert guard.touch(event) is True
    now[0] += 61.0
    assert guard.is_active(event) is False


def test_expired_states_are_pruned():
    now = [100.0]
    guard = OnboardingGuard(
        get_config=_config(
            onboarding_guard_minutes=1.0,
            onboarding_guard_messages=0,
        ),
        now=lambda: now[0],
    )
    first = _event("group:1")
    second = _event("group:2")

    assert guard.touch(first) is True
    now[0] += 61.0
    assert guard.touch(second) is True
    assert guard.is_active(first) is False


def test_per_group_tracking_is_independent():
    guard = OnboardingGuard(
        get_config=_config(
            onboarding_guard_minutes=0.0,
            onboarding_guard_messages=1,
        )
    )
    first = _event("group:1")
    second = _event("group:2")

    assert guard.touch(first) is True
    assert guard.touch(second) is True
    assert guard.touch(first) is False
    assert guard.touch(second) is False


def test_zero_limits_disable_onboarding():
    guard = OnboardingGuard(
        get_config=_config(
            onboarding_guard_minutes=0.0,
            onboarding_guard_messages=0,
        )
    )

    assert guard.touch(_event()) is False
