"""Platform-neutral terminal text safety helpers."""

from __future__ import annotations

import unicodedata

_DIRECTIONAL_CONTROL_CHARACTERS = frozenset(
    {
        "\u061c",  # Arabic Letter Mark
        "\u200e",  # Left-to-Right Mark
        "\u200f",  # Right-to-Left Mark
        "\u202a",  # Left-to-Right Embedding
        "\u202b",  # Right-to-Left Embedding
        "\u202c",  # Pop Directional Formatting
        "\u202d",  # Left-to-Right Override
        "\u202e",  # Right-to-Left Override
        "\u2066",  # Left-to-Right Isolate
        "\u2067",  # Right-to-Left Isolate
        "\u2068",  # First Strong Isolate
        "\u2069",  # Pop Directional Isolate
    }
)


def is_unsafe_terminal_character(character: str) -> bool:
    """Return whether one character can manipulate terminal text display."""
    return (
        unicodedata.category(character) in {"Cc", "Cs"}
        or character in _DIRECTIONAL_CONTROL_CHARACTERS
    )
