// SPDX-License-Identifier: MIT
pragma solidity ^0.8.6;

interface ITRC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address who) external view returns (uint256);
}

/**
 * TreasuryVaultV2
 * - Holds TRX (native) + TRC-20
 * - Only approved modules can spend via spendTRX/spendToken
 * - Per-module (and per-token) rate limits
 * - Guardian can pause; admin can unpause + emergency sweep (only when paused)
 *
 * Deploy with ADMIN = multisig in production.
 */
contract TreasuryVaultV2 {
    bytes4 private constant TRC20_TRANSFER_SELECTOR = 0xa9059cbb;

    uint8 public constant TOKEN_TRANSFER_MODE_STRICT = 0;
    uint8 public constant TOKEN_TRANSFER_MODE_BALANCE_DELTA = 1;

    // ----------------------------
    // Minimal roles
    // ----------------------------
    bytes32 public constant DEFAULT_ADMIN_ROLE = 0x00;
    bytes32 public constant GUARDIAN_ROLE      = keccak256("GUARDIAN_ROLE");
    bytes32 public constant MODULE_ROLE        = keccak256("MODULE_ROLE");

    mapping(bytes32 => mapping(address => bool)) private _roles;

    modifier onlyRole(bytes32 role) {
        require(_roles[role][msg.sender], "ACCESS_DENIED");
        _;
    }

    event RoleGranted(bytes32 indexed role, address indexed account, address indexed by);
    event RoleRevoked(bytes32 indexed role, address indexed account, address indexed by);

    function hasRole(bytes32 role, address account) external view returns (bool) {
        return _roles[role][account];
    }

    function grantRole(bytes32 role, address account) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(account != address(0), "ZERO_ADDR");
        if (!_roles[role][account]) {
            _roles[role][account] = true;
            emit RoleGranted(role, account, msg.sender);
        }
    }

    function revokeRole(bytes32 role, address account) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_roles[role][account]) {
            _roles[role][account] = false;
            emit RoleRevoked(role, account, msg.sender);
        }
    }

    function renounceRole(bytes32 role) external {
        if (_roles[role][msg.sender]) {
            _roles[role][msg.sender] = false;
            emit RoleRevoked(role, msg.sender, msg.sender);
        }
    }

    // ----------------------------
    // Reentrancy guard
    // ----------------------------
    uint256 private _lock = 1;
    modifier nonReentrant() {
        require(_lock == 1, "REENTRANCY");
        _lock = 2;
        _;
        _lock = 1;
    }

    // ----------------------------
    // Pause control
    // ----------------------------
    bool public paused;

    event Paused(address indexed by);
    event Unpaused(address indexed by);

    modifier whenNotPaused() {
        require(!paused, "PAUSED");
        _;
    }

    function pause() external onlyRole(GUARDIAN_ROLE) {
        if (!paused) {
            paused = true;
            emit Paused(msg.sender);
        }
    }

    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (paused) {
            paused = false;
            emit Unpaused(msg.sender);
        }
    }

    // ----------------------------
    // Module rate limits
    // ----------------------------
    struct ModuleLimit {
        bool enabled;
        uint48 periodSeconds;
        uint48 periodStart;
        uint160 maxPerPeriod;
        uint160 spentThisPeriod;
    }

    mapping(address => ModuleLimit) public trxLimit; // module => TRX limit
    mapping(address => mapping(address => ModuleLimit)) public tokenLimit; // token => module => limit

    event ModuleConfiguredTRX(address indexed module, bool enabled, uint256 maxPerPeriod, uint256 periodSeconds);
    event ModuleConfiguredToken(address indexed token, address indexed module, bool enabled, uint256 maxPerPeriod, uint256 periodSeconds);

    event SpendTRX(address indexed module, address indexed to, uint256 amount);
    event SpendToken(address indexed token, address indexed module, address indexed to, uint256 amount);
    event TokenTransferModeUpdated(address indexed token, uint8 indexed mode);
    event TokenTransferDiagnostic(
        address indexed token,
        address indexed to,
        uint256 amount,
        uint8 mode,
        bool callSuccess,
        uint256 returnDataLength,
        bool decodedBool,
        uint256 fromBefore,
        uint256 fromAfter,
        uint256 toBefore,
        uint256 toAfter
    );

    constructor(address admin, address guardian) {
        require(admin != address(0), "ZERO_ADMIN");
        _roles[DEFAULT_ADMIN_ROLE][admin] = true;
        emit RoleGranted(DEFAULT_ADMIN_ROLE, admin, msg.sender);

        if (guardian != address(0)) {
            _roles[GUARDIAN_ROLE][guardian] = true;
            emit RoleGranted(GUARDIAN_ROLE, guardian, msg.sender);
        }
    }

    receive() external payable {}

    bool public emitTransferDiagnostics;
    mapping(address => uint8) public tokenTransferMode;

    function setEmitTransferDiagnostics(bool enabled) external onlyRole(DEFAULT_ADMIN_ROLE) {
        emitTransferDiagnostics = enabled;
    }

    function setTokenTransferMode(address token, uint8 mode) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(token != address(0), "ZERO_TOKEN");
        require(mode == TOKEN_TRANSFER_MODE_STRICT || mode == TOKEN_TRANSFER_MODE_BALANCE_DELTA, "BAD_MODE");
        tokenTransferMode[token] = mode;
        emit TokenTransferModeUpdated(token, mode);
    }

    function configureModuleTRX(
        address module,
        bool enabled,
        uint256 maxPerPeriod,
        uint256 periodSeconds
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(module != address(0), "ZERO_MODULE");
        require(periodSeconds > 0, "BAD_PERIOD");

        if (!_roles[MODULE_ROLE][module]) {
            _roles[MODULE_ROLE][module] = true;
            emit RoleGranted(MODULE_ROLE, module, msg.sender);
        }

        ModuleLimit storage lim = trxLimit[module];
        lim.enabled = enabled;
        lim.periodSeconds = uint48(periodSeconds);
        lim.maxPerPeriod = uint160(maxPerPeriod);
        lim.periodStart = uint48(block.timestamp);
        lim.spentThisPeriod = 0;

        emit ModuleConfiguredTRX(module, enabled, maxPerPeriod, periodSeconds);
    }

    function configureModuleToken(
        address token,
        address module,
        bool enabled,
        uint256 maxPerPeriod,
        uint256 periodSeconds
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(token != address(0), "ZERO_TOKEN");
        require(module != address(0), "ZERO_MODULE");
        require(periodSeconds > 0, "BAD_PERIOD");

        if (!_roles[MODULE_ROLE][module]) {
            _roles[MODULE_ROLE][module] = true;
            emit RoleGranted(MODULE_ROLE, module, msg.sender);
        }

        ModuleLimit storage lim = tokenLimit[token][module];
        lim.enabled = enabled;
        lim.periodSeconds = uint48(periodSeconds);
        lim.maxPerPeriod = uint160(maxPerPeriod);
        lim.periodStart = uint48(block.timestamp);
        lim.spentThisPeriod = 0;

        emit ModuleConfiguredToken(token, module, enabled, maxPerPeriod, periodSeconds);
    }

    function _rollPeriodIfNeeded(ModuleLimit storage lim) internal {
        if (lim.periodStart == 0) return;
        uint256 start = uint256(lim.periodStart);
        uint256 period = uint256(lim.periodSeconds);
        if (block.timestamp >= start + period) {
            uint256 periodsElapsed = (block.timestamp - start) / period;
            uint256 newStart = start + periodsElapsed * period;
            lim.periodStart = uint48(newStart);
            lim.spentThisPeriod = 0;
        }
    }

    function _enforceAndAccrueTRX(address module, uint256 amount) internal {
        ModuleLimit storage lim = trxLimit[module];
        require(lim.enabled, "MODULE_DISABLED");
        _rollPeriodIfNeeded(lim);
        uint256 spent = uint256(lim.spentThisPeriod);
        uint256 cap = uint256(lim.maxPerPeriod);
        require(spent + amount <= cap, "TRX_LIMIT_EXCEEDED");
        lim.spentThisPeriod = uint160(spent + amount);
    }

    function _enforceAndAccrueToken(address token, address module, uint256 amount) internal {
        ModuleLimit storage lim = tokenLimit[token][module];
        require(lim.enabled, "MODULE_DISABLED");
        _rollPeriodIfNeeded(lim);
        uint256 spent = uint256(lim.spentThisPeriod);
        uint256 cap = uint256(lim.maxPerPeriod);
        require(spent + amount <= cap, "TOKEN_LIMIT_EXCEEDED");
        lim.spentThisPeriod = uint160(spent + amount);
    }

    function _safeTokenTransfer(address token, address to, uint256 amount) internal {
        uint8 mode = tokenTransferMode[token];
        if (mode == TOKEN_TRANSFER_MODE_BALANCE_DELTA) {
            _safeTokenTransferBalanceDelta(token, to, amount);
            return;
        }

        _safeTokenTransferStrict(token, to, amount);
    }

    function _safeTokenTransferStrict(address token, address to, uint256 amount) internal {
        uint256 fromBefore = 0;
        uint256 toBefore = 0;
        if (emitTransferDiagnostics) {
            fromBefore = ITRC20(token).balanceOf(address(this));
            toBefore = ITRC20(token).balanceOf(to);
        }

        (bool success, bytes memory data) = token.call(abi.encodeWithSelector(TRC20_TRANSFER_SELECTOR, to, amount));
        require(success, "TOKEN_SEND_FAIL");

        // Compatibility-safe handling for legacy tokens (for example legacy USDT)
        // that do not return a boolean on transfer.
        if (data.length == 0) {
            if (emitTransferDiagnostics) {
                uint256 fromAfterCompat = ITRC20(token).balanceOf(address(this));
                uint256 toAfterCompat = ITRC20(token).balanceOf(to);
                emit TokenTransferDiagnostic(
                    token,
                    to,
                    amount,
                    TOKEN_TRANSFER_MODE_STRICT,
                    success,
                    data.length,
                    true,
                    fromBefore,
                    fromAfterCompat,
                    toBefore,
                    toAfterCompat
                );
            }
            return;
        }

        require(data.length >= 32, "TOKEN_SEND_FAIL");
        bool decoded = abi.decode(data, (bool));
        require(decoded, "TOKEN_SEND_FAIL");

        if (emitTransferDiagnostics) {
            uint256 fromAfter = ITRC20(token).balanceOf(address(this));
            uint256 toAfter = ITRC20(token).balanceOf(to);
            emit TokenTransferDiagnostic(
                token,
                to,
                amount,
                TOKEN_TRANSFER_MODE_STRICT,
                success,
                data.length,
                decoded,
                fromBefore,
                fromAfter,
                toBefore,
                toAfter
            );
        }
    }

    function _safeTokenTransferBalanceDelta(address token, address to, uint256 amount) internal {
        uint256 fromBefore = ITRC20(token).balanceOf(address(this));
        uint256 toBefore = ITRC20(token).balanceOf(to);

        (bool success, bytes memory data) = token.call(abi.encodeWithSelector(TRC20_TRANSFER_SELECTOR, to, amount));
        require(success, "TOKEN_SEND_FAIL");

        bool decoded = true;
        if (data.length >= 32) {
            decoded = abi.decode(data, (bool));
        }

        uint256 fromAfter = ITRC20(token).balanceOf(address(this));
        uint256 toAfter = ITRC20(token).balanceOf(to);

        if (emitTransferDiagnostics) {
            emit TokenTransferDiagnostic(
                token,
                to,
                amount,
                TOKEN_TRANSFER_MODE_BALANCE_DELTA,
                success,
                data.length,
                decoded,
                fromBefore,
                fromAfter,
                toBefore,
                toAfter
            );
        }

        require(fromBefore >= amount, "TOKEN_SEND_FAIL");
        require(fromAfter + amount == fromBefore, "TOKEN_SEND_FAIL");
        require(toAfter >= toBefore, "TOKEN_SEND_FAIL");
        require(toAfter - toBefore == amount, "TOKEN_SEND_FAIL");
    }

    function spendTRX(address payable to, uint256 amount)
        external
        nonReentrant
        whenNotPaused
        onlyRole(MODULE_ROLE)
    {
        require(to != address(0), "ZERO_TO");
        _enforceAndAccrueTRX(msg.sender, amount);

        (bool ok, ) = to.call{value: amount}("");
        require(ok, "TRX_SEND_FAIL");

        emit SpendTRX(msg.sender, to, amount);
    }

    function spendToken(address token, address to, uint256 amount)
        external
        nonReentrant
        whenNotPaused
        onlyRole(MODULE_ROLE)
    {
        require(token != address(0), "ZERO_TOKEN");
        require(to != address(0), "ZERO_TO");
        _enforceAndAccrueToken(token, msg.sender, amount);

        _safeTokenTransfer(token, to, amount);

        emit SpendToken(token, msg.sender, to, amount);
    }

    // Emergency recovery (admin-only, only when paused)
    event EmergencySweepTRX(address indexed to, uint256 amount);
    event EmergencySweepToken(address indexed token, address indexed to, uint256 amount);

    function emergencySweepTRX(address payable to, uint256 amount)
        external
        nonReentrant
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        require(paused, "NOT_PAUSED");
        require(to != address(0), "ZERO_TO");
        (bool ok, ) = to.call{value: amount}("");
        require(ok, "TRX_SEND_FAIL");
        emit EmergencySweepTRX(to, amount);
    }

    function emergencySweepToken(address token, address to, uint256 amount)
        external
        nonReentrant
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        require(paused, "NOT_PAUSED");
        require(token != address(0), "ZERO_TOKEN");
        require(to != address(0), "ZERO_TO");
        _safeTokenTransfer(token, to, amount);
        emit EmergencySweepToken(token, to, amount);
    }

    function trxBalance() external view returns (uint256) { return address(this).balance; }
    function tokenBalance(address token) external view returns (uint256) { return ITRC20(token).balanceOf(address(this)); }
}
