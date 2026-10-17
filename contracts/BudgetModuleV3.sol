// SPDX-License-Identifier: MIT
pragma solidity ^0.8.6;

interface ITreasuryVaultV2 {
    function spendTRX(address payable to, uint256 amount) external;
    function spendToken(address token, address to, uint256 amount) external;
}

/**
 * BudgetModuleV3
 * - Payee pulls within capPerPeriod, enforced here
 * - Payout is executed by the Vault (single hop, no transfer() gas-stipend issues)
 *
 * IMPORTANT: Vault must configure this contract as an enabled module with its own limits.
 */
contract BudgetModuleV3 {
    address public treasury;
    address public payee;
    address public token; // address(0) => TRX
    uint256 public capPerPeriod;

    uint256 public periodSeconds; // default 30 days
    uint256 public epochStart;
    uint256 public spentThisEpoch;

    bool public paused;

    event Claimed(address indexed payee, address indexed token, uint256 amount);
    event PayeeChanged(address indexed oldPayee, address indexed newPayee);
    event TreasuryChanged(address indexed oldTreasury, address indexed newTreasury);
    event TokenChanged(address indexed oldToken, address indexed newToken);
    event CapChanged(uint256 oldCap, uint256 newCap);
    event PeriodChanged(uint256 oldPeriod, uint256 newPeriod);
    event Paused(address indexed by);
    event Unpaused(address indexed by);

    modifier onlyPayee() {
        require(msg.sender == payee, "NOT_PAYEE");
        _;
    }

    modifier onlyAdmin() {
        // For MVP: admin == payee. If you want separate admin, add an admin address.
        require(msg.sender == payee, "NOT_ADMIN");
        _;
    }

    modifier whenNotPaused() {
        require(!paused, "PAUSED");
        _;
    }

    constructor(
        address _treasury,
        address _payee,
        address _token,
        uint256 _capPerPeriod,
        uint256 _periodSeconds
    ) {
        require(_treasury != address(0), "BAD_TREASURY");
        require(_payee != address(0), "BAD_PAYEE");
        require(_periodSeconds > 0, "BAD_PERIOD");

        treasury = _treasury;
        payee = _payee;
        token = _token;
        capPerPeriod = _capPerPeriod;

        periodSeconds = _periodSeconds;
        epochStart = block.timestamp;
    }

    function pause() external onlyAdmin {
        if (!paused) {
            paused = true;
            emit Paused(msg.sender);
        }
    }

    function unpause() external onlyAdmin {
        if (paused) {
            paused = false;
            emit Unpaused(msg.sender);
        }
    }

    function setPayee(address newPayee) external onlyAdmin {
        require(newPayee != address(0), "BAD_PAYEE");
        address old = payee;
        payee = newPayee;
        emit PayeeChanged(old, newPayee);
    }

    function setTreasury(address newTreasury) external onlyAdmin {
        require(newTreasury != address(0), "BAD_TREASURY");
        address old = treasury;
        treasury = newTreasury;
        emit TreasuryChanged(old, newTreasury);
    }

    function setToken(address newToken) external onlyAdmin {
        address old = token;
        token = newToken;
        emit TokenChanged(old, newToken);
    }

    function setCap(uint256 newCapPerPeriod) external onlyAdmin {
        uint256 old = capPerPeriod;
        capPerPeriod = newCapPerPeriod;
        emit CapChanged(old, newCapPerPeriod);
    }

    function setPeriod(uint256 newPeriodSeconds) external onlyAdmin {
        require(newPeriodSeconds > 0, "BAD_PERIOD");
        uint256 old = periodSeconds;
        periodSeconds = newPeriodSeconds;
        // reset accounting on period change
        epochStart = block.timestamp;
        spentThisEpoch = 0;
        emit PeriodChanged(old, newPeriodSeconds);
    }

    function _rollEpochIfNeeded() internal {
        uint256 start = epochStart;
        uint256 p = periodSeconds;

        if (block.timestamp >= start + p) {
            uint256 elapsed = (block.timestamp - start) / p;
            epochStart = start + elapsed * p;
            spentThisEpoch = 0;
        }
    }

    function claim(uint256 amount)
        external
        onlyPayee
        whenNotPaused
    {
        require(amount > 0, "BAD_AMOUNT");

        _rollEpochIfNeeded();

        uint256 newSpent = spentThisEpoch + amount;
        require(newSpent <= capPerPeriod, "CAP_EXCEEDED");
        spentThisEpoch = newSpent;

        if (token == address(0)) {
            ITreasuryVaultV2(treasury).spendTRX(payable(payee), amount);
        } else {
            ITreasuryVaultV2(treasury).spendToken(token, payee, amount);
        }

        emit Claimed(payee, token, amount);
    }
}