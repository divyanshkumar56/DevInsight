"""Small, focused text helpers -- examples of clean, low-risk functions."""

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Convert arbitrary text into a URL-friendly slug."""
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def truncate(text: str, limit: int = 80, suffix: str = "...") -> str:
    """Shorten text to `limit` characters, appending `suffix` when cut."""
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)] + suffix


def word_count(text: str) -> int:
    """Return the number of whitespace-separated words."""
    return len(text.split())


def is_palindrome(text: str) -> bool:
    """Check whether text reads the same forwards and backwards (ignoring case/spaces)."""
    cleaned = "".join(ch.lower() for ch in text if ch.isalnum())
    return cleaned == cleaned[::-1]


def title_case(text: str, keep_lower=("a", "an", "the", "of", "and")) -> str:
    """Title-case a sentence while keeping small words lowercase."""
    words = text.split()
    result = [w.capitalize() if i == 0 or w not in keep_lower else w for i, w in enumerate(words)]
    return " ".join(result)

