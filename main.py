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
    }

    modifier nonReentrant() {
        if (_laneLock == 1) revert PXG_Reentry();
        _laneLock = 1;
        _;
        _laneLock = 0;
    }

    constructor() {
        pitMaster = msg.sender;
        oracleRelay = msg.sender;
        ADDRESS_A = {ADDR_A};
        ADDRESS_B = {ADDR_B};
        ADDRESS_C = {ADDR_C};
        if (ADDRESS_A == address(0) || ADDRESS_B == address(0) || ADDRESS_C == address(0)) {
            revert PXG_ZeroAddr();
        }
        laneFeeBps = 277;
        heatDecayBps = 1_025;
        seasonId = 1;
        seasonOpenedAt = uint64(block.timestamp);
        _bootstrapModes();
        _rollSeason(false);
    }

    receive() external payable {
        emit Tipped(msg.sender, msg.value, keccak256("popXG.tip"));
    }

    fallback() external payable {
        revert PXG_FallbackBlocked();
    }

    function transferPit(address next) external onlyPitMaster {
        if (next == address(0)) revert PXG_ZeroAddr();
        address prev = pitMaster;
        pitMaster = next;
        emit PitMoved(prev, next);
    }

    function setOracleRelay(address next) external onlyPitMaster {
        if (next == address(0)) revert PXG_ZeroAddr();
        address prev = oracleRelay;
        oracleRelay = next;
        emit OracleRelaySet(prev, next);
    }

    function setGridFrozen(bool on) external onlyPitMaster {
        gridFrozen = on;
        emit GridFreeze(on, msg.sender);
    }

    function tuneFees(uint16 newLaneFeeBps, uint16 newHeatDecayBps) external onlyPitMaster {
        if (newLaneFeeBps > PXG_FEE_CAP_BPS) revert PXG_BadFee(newLaneFeeBps, PXG_FEE_CAP_BPS);
        if (newHeatDecayBps > 2_500) revert PXG_BadFee(newHeatDecayBps, 2_500);
        laneFeeBps = newLaneFeeBps;
        heatDecayBps = newHeatDecayBps;
        emit FeeTuned(newLaneFeeBps, newHeatDecayBps, msg.sender);
    }

    function pinMode(uint32 mode, bytes32 label, uint16 cellTarget, uint16 feeBiasBps, uint32 scoreMul, uint64 durBias, bool enabled)
        external
        onlyPitMaster
    {
        modeCatalog[uint8(mode)] = ModeRecipe({
            label: label,
            cellTarget: cellTarget,
            feeBiasBps: feeBiasBps,
            scoreMultiplier: scoreMul,
            durationBias: durBias,
            enabled: enabled
        });
        emit ModePinned(mode, label, enabled);
    }

    function forceSeasonRoll() external onlyPitMaster {
        _rollSeason(true);
    }

    function openRun(uint32 mode, uint128 entryWei, bytes32 laneSalt) external payable laneOpen nonReentrant returns (uint256 runId) {
        ModeRecipe memory recipe = modeCatalog[uint8(mode)];
        if (!recipe.enabled) revert PXG_ModeOff(mode);
        if (usedRunSalts[laneSalt]) revert PXG_SaltUsed(laneSalt);
        if (entryWei < PXG_MIN_ENTRY || entryWei > PXG_MAX_ENTRY) revert PXG_BadEntry(entryWei);
        if (msg.value != entryWei) revert PXG_BadEntry(msg.value);

        usedRunSalts[laneSalt] = true;
        uint64 closes = uint64(block.timestamp) + _runDuration(mode);
        runId = ++nextRunId;

        uint128 feeSlice = uint128((uint256(entryWei) * uint256(laneFeeBps + recipe.feeBiasBps)) / PXG_BPS);
        if (feeSlice > entryWei) feeSlice = entryWei / 3;
        uint128 potPart = entryWei - feeSlice;
        lifetimeFees += feeSlice;
        seasonPot += feeSlice / 2;

        _runs[runId] = RunLane({
            openedAt: uint64(block.timestamp),
            closesAt: closes,
            seasonSnap: seasonId,
            mode: mode,
            entryWei: entryWei,
            potWei: potPart,
            poppedCount: 0,
            comboHigh: 0,
            feverHits: 0,
            settled: false,
            jackpotArmed: _jackpotRoll(laneSalt, runId),
            laneSalt: laneSalt,
            opener: msg.sender
        });

        _joinInternal(runId, msg.sender, entryWei, true);
        emit Opened(runId, msg.sender, mode, entryWei, closes);
    }

    function joinRun(uint256 runId) external payable laneOpen nonReentrant {
        RunLane storage lane = _runs[runId];
        if (lane.opener == address(0)) revert PXG_RunMissing(runId);
        if (lane.settled) revert PXG_RunSettled(runId);
        if (block.timestamp >= lane.closesAt) revert PXG_RunClosed(runId);
        if (msg.value != lane.entryWei) revert PXG_BadEntry(msg.value);

        uint128 feeSlice = uint128((uint256(msg.value) * uint256(laneFeeBps)) / PXG_BPS);
        uint128 potPart = uint128(msg.value) - feeSlice;
        lane.potWei += potPart;
        lifetimeFees += feeSlice;
        seasonPot += feeSlice / 2;

        _joinInternal(runId, msg.sender, lane.entryWei, false);
        emit Joined(runId, msg.sender, lane.entryWei);
    }

    function popCell(uint256 runId, uint16 cell) external laneOpen nonReentrant {
        RunLane storage lane = _runs[runId];
        if (lane.opener == address(0)) revert PXG_RunMissing(runId);
        if (lane.settled) revert PXG_RunSettled(runId);
        if (block.timestamp >= lane.closesAt) revert PXG_RunClosed(runId);
        if (cell >= PXG_CELL_COUNT) revert PXG_CellBounds(cell);

        PlayerRun storage pr = _playerRuns[runId][msg.sender];
        if (pr.joinedAt == 0) revert PXG_NotInRun(msg.sender, runId);

        uint64 ready = pr.lastAction + PXG_POP_COOLDOWN;
        if (block.timestamp < ready) revert PXG_Cooldown(ready);

        CellState storage cs = _cells[runId][cell];
        if (cs.popper != address(0)) revert PXG_CellPopped(cell);

        uint32 heat = _heatForCell(runId, cell, lane.laneSalt);
        cs.heat = heat;
        cs.popper = msg.sender;
        cs.poppedAt = uint64(block.timestamp);
        cs.lootTier = _lootTier(heat);
        cs.isJackpotCell = lane.jackpotArmed && _isJackpotCell(runId, cell);

        lane.poppedCount += 1;
        pr.cellsPopped += 1;
        pr.lastAction = uint64(block.timestamp);
        lastPopAt[msg.sender] = pr.lastAction;

        if (_comboContinue(runId, cell, msg.sender)) {
            if (pr.combo < PXG_MAX_COMBO) pr.combo += 1;
        } else {
            pr.combo = 1;
        }
        if (pr.combo > pr.bestCombo) pr.bestCombo = pr.combo;
        if (pr.combo > lane.comboHigh) lane.comboHigh = pr.combo;

        bool feverOn = false;
        if (pr.combo >= PXG_FEVER_THRESHOLD) {
            feverOn = true;
            pr.feverActive = true;
            lane.feverHits += 1;
            feverActivations += 1;
        }

        uint128 scoreAdd = _scoreAdd(lane.mode, heat, pr.combo, pr.feverActive);
        pr.score += scoreAdd;
        lifetimeScore[msg.sender] += scoreAdd;
        seasonScore[lane.seasonSnap][msg.sender] += scoreAdd;
        globalPopNonce += 1;

        if (cs.isJackpotCell && lane.potWei > 0) {
            uint128 slice = uint128((uint256(lane.potWei) * PXG_JACKPOT_SLICE_BPS) / PXG_BPS);
            pendingWei[msg.sender] += slice;
            lane.potWei -= slice;
            emit JackpotTagged(runId, cell, slice);
        }

        _maybeAchievement(msg.sender, lane.seasonSnap, pr);
        _bumpLeader(lane.seasonSnap, msg.sender, seasonScore[lane.seasonSnap][msg.sender]);

        emit Popped(runId, msg.sender, cell, heat, scoreAdd);
        emit ComboHeat(runId, msg.sender, pr.combo, feverOn);
    }

    function settleRun(uint256 runId) external laneOpen nonReentrant {
        RunLane storage lane = _runs[runId];
        if (lane.opener == address(0)) revert PXG_RunMissing(runId);
        if (lane.settled) revert PXG_RunSettled(runId);
        if (block.timestamp < lane.closesAt && msg.sender != pitMaster) revert PXG_RunOpen(runId);

        lane.settled = true;
        address winner = _pickWinner(runId);
        uint128 potOut = lane.potWei;

        if (winner != address(0) && potOut > 0) {
            uint128 slice = uint128((uint256(potOut) * PXG_RUNNER_UP_BPS) / PXG_BPS);
            pendingWei[winner] += slice;
            potOut -= slice;
            creditLedger[winner] += uint256(potOut) * 1e12;
        }

        emit Settled(runId, winner, potOut, lane.comboHigh);
    }

    function claimRun(uint256 runId) external nonReentrant {
        RunLane storage lane = _runs[runId];
        if (lane.opener == address(0)) revert PXG_RunMissing(runId);
        if (!lane.settled) revert PXG_RunOpen(runId);

        PlayerRun storage pr = _playerRuns[runId][msg.sender];
        if (pr.joinedAt == 0) revert PXG_NotInRun(msg.sender, runId);
        if (pr.claimed) revert PXG_AlreadyClaimed(runId, msg.sender);

        uint64 ready = lane.closesAt + PXG_CLAIM_DELAY;
        if (block.timestamp < ready) revert PXG_Cooldown(ready);

        pr.claimed = true;
        uint256 weiOut = pendingWei[msg.sender];
        uint128 creditOut = uint128(creditLedger[msg.sender] / 1e12);
        if (weiOut == 0 && creditOut == 0) revert PXG_NothingToClaim(msg.sender);

        pendingWei[msg.sender] = 0;
        if (creditOut > 0) {
            creditLedger[msg.sender] -= uint256(creditOut) * 1e12;
        }

        if (weiOut > 0) {
            _safeSend(payable(msg.sender), weiOut);
        }
        emit Claimed(runId, msg.sender, weiOut, creditOut);
    }

    function withdrawCredits(uint256 amount) external nonReentrant {
        if (amount == 0) revert PXG_NothingToClaim(msg.sender);
        if (creditLedger[msg.sender] < amount) revert PXG_NothingToClaim(msg.sender);
        creditLedger[msg.sender] -= amount;
        uint256 micro = amount;
        _safeSend(payable(msg.sender), micro / 1e12);
        emit Credited(msg.sender, amount, keccak256("popXG.withdraw"));
    }

    function donateSeason() external payable laneOpen nonReentrant {
        if (msg.value == 0) revert PXG_BadEntry(0);
        seasonPot += uint128(msg.value);
        emit Tipped(msg.sender, msg.value, keccak256("popXG.season"));
    }

    function sweepDust(address payable to, uint256 amount, bytes32 tag) external onlyPitMaster nonReentrant {
        if (to == address(0)) revert PXG_ZeroAddr();
        uint256 bal = address(this).balance;
        if (amount > bal) amount = bal;
        if (amount == 0) revert PXG_PotDry();
        _safeSend(to, amount);
        emit Swept(to, amount, tag);
    }

    function laneDigest(uint256 runId, address player) external view returns (bytes32) {
        (bytes32 hA, bytes32 hB) = _splitDigest(runId, player);
        return keccak256(abi.encodePacked(hA, hB, PXG_DOMAIN, block.chainid));
    }

    function runInfo(uint256 runId)
        external
        view
        returns (
            uint64 openedAt,
            uint64 closesAt,
            uint32 mode,
            uint128 entryWei,
            uint128 potWei,
            uint128 popped,
            bool settled,
            address opener
        )
    {
        RunLane storage lane = _runs[runId];
        return (lane.openedAt, lane.closesAt, lane.mode, lane.entryWei, lane.potWei, lane.poppedCount, lane.settled, lane.opener);
    }

    function playerInfo(uint256 runId, address player)
        external
        view
        returns (uint128 score, uint16 combo, uint16 bestCombo, uint128 cellsPopped, bool claimed, bool feverActive)
    {
        PlayerRun storage pr = _playerRuns[runId][player];
        return (pr.score, pr.combo, pr.bestCombo, pr.cellsPopped, pr.claimed, pr.feverActive);
    }

    function cellInfo(uint256 runId, uint16 cell)
        external
        view
        returns (uint32 heat, address popper, uint32 lootTier, bool jackpotCell)
    {
        CellState storage cs = _cells[runId][cell];
        return (cs.heat, cs.popper, cs.lootTier, cs.isJackpotCell);
    }

    function seasonBoard(uint64 sid) external view returns (address[32] memory leaders, uint256[32] memory scores) {
        return (seasonLeaders[sid], seasonLeaderScores[sid]);
    }

    function achievementBitmap(uint64 sid, address player) external view returns (uint256) {
        return seasonAchievements[sid][player];
    }

    function _joinInternal(uint256 runId, address player, uint128 paid, bool isOpener) internal {
        PlayerRun storage pr = _playerRuns[runId][player];
        if (pr.joinedAt != 0) {
            if (!isOpener) revert PXG_AlreadyClaimed(runId, player);
            return;
        }
        pr.joinedAt = uint64(block.timestamp);
        pr.lastAction = pr.joinedAt;
        creditLedger[player] += uint256(paid) / 1e6;
    }

    function _rollSeason(bool force) internal {
        if (!force && block.timestamp < seasonOpenedAt + PXG_SEASON_LENGTH) return;
        uint128 carry = seasonPot / 3;
        seasonPot = seasonPot - carry;
        seasonId += 1;
        seasonOpenedAt = uint64(block.timestamp);
        emit SeasonRolled(seasonId, seasonOpenedAt, carry);
    }

    function _bootstrapModes() internal {
        modeCatalog[uint8(PXG_MODE_BLITZ)] = ModeRecipe({
            label: keccak256("popXG.blitz"),
            cellTarget: PXG_BLITZ_CELLS,
            feeBiasBps: 33,
            scoreMultiplier: 1_150,
            durationBias: 19 minutes,
            enabled: true
        });
        modeCatalog[uint8(PXG_MODE_MARATHON)] = ModeRecipe({
            label: keccak256("popXG.marathon"),
            cellTarget: PXG_MARATHON_CELLS,
            feeBiasBps: 12,
            scoreMultiplier: 2_400,
            durationBias: 52 minutes,
            enabled: true
        });
        modeCatalog[uint8(PXG_MODE_FEVER)] = ModeRecipe({
