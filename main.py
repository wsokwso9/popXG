"""One-off generator for contracts/popXG.sol — not part of runtime."""
from __future__ import annotations

import secrets
from pathlib import Path

from eth_utils import keccak


def checksum(addr_bytes: bytes) -> str:
    addr_hex = addr_bytes.hex()
    h = keccak(text=addr_hex).hex()
    out = "0x"
    for i, c in enumerate(addr_hex):
        if c in "0123456789":
            out += c
        else:
            out += c.upper() if int(h[i], 16) >= 8 else c.lower()
    return out


def main() -> None:
    a, b, c = [checksum(secrets.token_bytes(20)) for _ in range(3)]
    for addr in (a, b, c):
        h = addr[2:]
        assert any(x.isupper() for x in h)
        assert any(x.islower() for x in h)
        assert any(x.isdigit() for x in h)

    out = Path(__file__).resolve().parents[1] / "contracts" / "popXG.sol"
    body = (
        SOL_TEMPLATE.replace("{ADDR_A}", a)
        .replace("{ADDR_B}", b)
        .replace("{ADDR_C}", c)
    )
    out.write_text(body, encoding="utf-8")
    lines = body.count("\n") + 1
    print(f"Wrote {out} ({lines} lines)")


SOL_TEMPLATE = r'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title popXG — arcade bubble grid seasons
/// @notice Codename: extrusion lane. Stake into timed runs, chain pops for combo heat, pull credits when the lane cools.
/// @dev Degen-friendly pacing; not a vault. pitMaster tunes fees; players claim winnings themselves.

contract popXG {
    // Notes: vending machines in orbit still owe gravity a receipt.

    address public immutable ADDRESS_A;
