"""Typed environment settings.

A malformed value fails with the variable's name ("VAYL_PORT must be an integer, got 'eighty'")
instead of a bare `invalid literal for int()` that leaves the operator guessing which setting broke.
An unset or empty variable means "use the default".
"""
import os


def env_int(name, default):
    return _parse(name, default, int, "an integer")


def env_float(name, default):
    return _parse(name, default, float, "a number")


_TRUE, _FALSE = ("1", "on", "true", "yes"), ("0", "off", "false", "no")


def env_bool(name, default):
    """on/off/true/false/yes/no/1/0 (any case). Anything else fails, so a typo in a security switch
    can't silently leave it in its default state."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE or raw in _FALSE:
        return raw in _TRUE
    raise ValueError(f"{name} must be on or off, got {raw!r}")


def _parse(name, default, kind, what):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return kind(raw)
    except ValueError:
        raise ValueError(f"{name} must be {what}, got {raw!r}") from None
