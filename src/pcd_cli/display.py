"""Render filesystem and user-supplied text without terminal control sequences."""

_CONTROL_ESCAPES = {code: f"\\x{code:02x}" for code in (*range(32), *range(127, 160))}
_CONTROL_ESCAPES.update({ord("\n"): r"\n", ord("\r"): r"\r", ord("\t"): r"\t"})
_CONTROL_ESCAPES.update(
    {code: f"\\u{code:04x}" for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))}
)


def terminal_text(text: str) -> str:
    """Escape controls in human-readable output; leave stored values unchanged."""
    if text.isprintable():
        return text
    return text.translate(_CONTROL_ESCAPES)
