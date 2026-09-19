from __future__ import annotations

import secrets
import time


def _encode_crockford(value: int, length: int) -> str:
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    chars: list[str] = []
    for _ in range(length):
        chars.append(alphabet[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid() -> str:
    timestamp = int(time.time() * 1000) & ((1 << 48) - 1)
    random_bits = int.from_bytes(secrets.token_bytes(10), "big")
    return _encode_crockford((timestamp << 80) | random_bits, 26)
