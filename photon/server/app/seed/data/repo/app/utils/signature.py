from __future__ import annotations

import secrets


def generate_signing_secret() -> str:
    return f"whsec_{secrets.token_hex(20)}"
