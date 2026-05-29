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
    address public immutable ADDRESS_B;
    address public immutable ADDRESS_C;

    uint256 public constant PXG_BUILD = 19;
    uint16 public constant PXG_BPS = 10_000;
    uint16 public constant PXG_FEE_CAP_BPS = 612;
    uint16 public constant PXG_JACKPOT_SLICE_BPS = 88;
    uint16 public constant PXG_RUNNER_UP_BPS = 1_450;
    uint16 public constant PXG_GRID_SIDE = 9;
    uint16 public constant PXG_CELL_COUNT = 81;
    uint16 public constant PXG_MAX_COMBO = 24;
    uint16 public constant PXG_FEVER_THRESHOLD = 7;
    uint16 public constant PXG_BLITZ_CELLS = 36;
    uint16 public constant PXG_MARATHON_CELLS = 81;
    uint32 public constant PXG_SEASON_TAG = 0x7A4E9031;
    uint32 public constant PXG_MODE_BLITZ = 1;
    uint32 public constant PXG_MODE_MARATHON = 2;
    uint32 public constant PXG_MODE_FEVER = 3;
    uint64 public constant PXG_RUN_COOLDOWN = 41 minutes + 17 seconds;
    uint64 public constant PXG_POP_COOLDOWN = 11 seconds;
    uint64 public constant PXG_CLAIM_DELAY = 3 hours + 22 minutes;
    uint64 public constant PXG_SEASON_LENGTH = 6 days + 13 hours;
    uint128 public constant PXG_MIN_ENTRY = 0.00042 ether;
    uint128 public constant PXG_MAX_ENTRY = 0.42 ether;
    uint256 public constant PXG_ACHIEVEMENT_SLOTS = 64;

    bytes32 public constant PXG_DOMAIN = keccak256("popXG.extrusion.domain.v3");
    bytes32 public constant PXG_MODEHASH = keccak256("popXG.MODE(uint32 mode,uint64 seasonId,uint256 runId)");

    address public pitMaster;
    address public oracleRelay;
    bool public gridFrozen;
    uint256 private _laneLock;

    uint64 public seasonId;
    uint64 public seasonOpenedAt;
    uint16 public laneFeeBps;
    uint16 public heatDecayBps;
    uint128 public seasonPot;
    uint128 public lifetimeFees;

    uint256 public nextRunId;
    uint256 public globalPopNonce;
    uint256 public feverActivations;

    mapping(uint256 => RunLane) private _runs;
    mapping(uint256 => mapping(uint16 => CellState)) private _cells;
    mapping(uint256 => mapping(address => PlayerRun)) private _playerRuns;
    mapping(address => uint256) public creditLedger;
    mapping(address => uint256) public pendingWei;
    mapping(address => uint64) public lastPopAt;
    mapping(address => uint256) public lifetimeScore;
    mapping(uint64 => mapping(address => uint256)) public seasonScore;
    mapping(uint64 => mapping(address => uint256)) public seasonAchievements;
    mapping(uint64 => address[32]) public seasonLeaders;
    mapping(uint64 => uint256[32]) public seasonLeaderScores;
    mapping(uint8 => ModeRecipe) public modeCatalog;
    mapping(bytes32 => bool) public usedRunSalts;
    mapping(address => uint256) private _withdrawNonce;

    struct RunLane {
        uint64 openedAt;
        uint64 closesAt;
        uint64 seasonSnap;
        uint32 mode;
        uint128 entryWei;
        uint128 potWei;
        uint128 poppedCount;
        uint16 comboHigh;
        uint8 feverHits;
        bool settled;
        bool jackpotArmed;
        bytes32 laneSalt;
        address opener;
    }

    struct CellState {
        uint32 heat;
        uint32 lootTier;
        uint64 poppedAt;
        address popper;
        bool isJackpotCell;
    }

    struct PlayerRun {
        uint128 score;
        uint16 combo;
        uint16 bestCombo;
        uint64 joinedAt;
        uint64 lastAction;
        uint128 cellsPopped;
        bool claimed;
        bool feverActive;
    }

    struct ModeRecipe {
        bytes32 label;
        uint16 cellTarget;
        uint16 feeBiasBps;
        uint32 scoreMultiplier;
        uint64 durationBias;
        bool enabled;
    }

    error PXG_NotPitMaster(address who);
    error PXG_ZeroAddr();
    error PXG_Frozen();
    error PXG_Reentry();
    error PXG_BadFee(uint16 got, uint16 cap);
    error PXG_BadEntry(uint256 got);
    error PXG_RunMissing(uint256 runId);
    error PXG_RunClosed(uint256 runId);
    error PXG_RunSettled(uint256 runId);
    error PXG_RunOpen(uint256 runId);
    error PXG_NotInRun(address who, uint256 runId);
    error PXG_CellBounds(uint16 cell);
    error PXG_CellPopped(uint16 cell);
    error PXG_Cooldown(uint64 ready);
    error PXG_NothingToClaim(address who);
    error PXG_AlreadyClaimed(uint256 runId, address who);
    error PXG_ModeOff(uint32 mode);
    error PXG_SaltUsed(bytes32 salt);
    error PXG_PotDry();
    error PXG_TransferFail();
    error PXG_EthUnexpected();
    error PXG_FallbackBlocked();

    event Tipped(address indexed from, uint256 amount, bytes32 memo);
    event PitMoved(address indexed prev, address indexed next);
    event OracleRelaySet(address indexed prev, address indexed next);
    event GridFreeze(bool indexed on, address indexed by);
    event FeeTuned(uint16 laneFeeBps, uint16 heatDecayBps, address indexed by);
    event SeasonRolled(uint64 indexed seasonId, uint64 openedAt, uint128 carriedPot);
    event ModePinned(uint32 indexed mode, bytes32 label, bool enabled);
    event Opened(uint256 indexed runId, address indexed opener, uint32 mode, uint128 entryWei, uint64 closesAt);
    event Joined(uint256 indexed runId, address indexed player, uint128 paid);
    event Popped(uint256 indexed runId, address indexed player, uint16 cell, uint32 heat, uint128 scoreAdd);
    event ComboHeat(uint256 indexed runId, address indexed player, uint16 combo, bool feverOn);
    event JackpotTagged(uint256 indexed runId, uint16 cell, uint128 potSlice);
    event Settled(uint256 indexed runId, address indexed winner, uint128 potOut, uint16 peakCombo);
    event Claimed(uint256 indexed runId, address indexed player, uint256 weiOut, uint128 creditOut);
    event Credited(address indexed player, uint256 delta, bytes32 reason);
    event Swept(address indexed to, uint256 amount, bytes32 tag);
    event Achievement(address indexed player, uint64 seasonId, uint8 slot, uint256 bitmap);

    modifier onlyPitMaster() {
        if (msg.sender != pitMaster) revert PXG_NotPitMaster(msg.sender);
        _;
    }

    modifier laneOpen() {
        if (gridFrozen) revert PXG_Frozen();
        _;
