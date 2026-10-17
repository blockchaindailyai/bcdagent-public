// SPDX-License-Identifier: MIT
pragma solidity ^0.8.6;

contract SubscriptionRegistryTRXV3 {
    address public owner;
    address payable public treasury;

    uint256 public trxUsdPriceE6;     // micro-USD per TRX
    uint256 public monthlyPriceUsdE6; // default $50 => 50_000_000

    mapping(address => uint256) public accessUntil;

    bool public paused;

    event Subscribed(address indexed user, uint256 months, uint256 paidSun, uint256 newAccessUntil);
    event TreasuryChanged(address indexed oldTreasury, address indexed newTreasury);
    event PriceChanged(uint256 oldPrice, uint256 newPrice);
    event MonthlyPriceChanged(uint256 oldMonthly, uint256 newMonthly);
    event Paused();
    event Unpaused();
    event OwnerChanged(address indexed oldOwner, address indexed newOwner);

    uint256 private _lock = 1;
    modifier nonReentrant() {
        require(_lock == 1, "REENTRANCY");
        _lock = 2;
        _;
        _lock = 1;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "NOT_OWNER");
        _;
    }

    modifier whenNotPaused() {
        require(!paused, "PAUSED");
        _;
    }

    constructor(address _owner, address payable _treasury, uint256 _trxUsdPriceE6) {
        require(_owner != address(0), "BAD_OWNER");
        require(_treasury != address(0), "BAD_TREASURY");
        require(_trxUsdPriceE6 > 0, "BAD_PRICE");

        owner = _owner;
        treasury = _treasury;
        trxUsdPriceE6 = _trxUsdPriceE6;
        monthlyPriceUsdE6 = 50_000_000; // $50 default
    }

    receive() external payable {}

    function setTreasury(address payable newTreasury) external onlyOwner {
        require(newTreasury != address(0), "BAD_TREASURY");
        address old = treasury;
        treasury = newTreasury;
        emit TreasuryChanged(old, newTreasury);
    }

    function setTrxUsdPriceE6(uint256 newPrice) external onlyOwner {
        require(newPrice > 0, "BAD_PRICE");
        uint256 old = trxUsdPriceE6;
        trxUsdPriceE6 = newPrice;
        emit PriceChanged(old, newPrice);
    }

    function setMonthlyPriceUsdE6(uint256 newMonthly) external onlyOwner {
        require(newMonthly > 0, "BAD_MONTHLY");
        uint256 old = monthlyPriceUsdE6;
        monthlyPriceUsdE6 = newMonthly;
        emit MonthlyPriceChanged(old, newMonthly);
    }

    function pause() external onlyOwner { paused = true; emit Paused(); }
    function unpause() external onlyOwner { paused = false; emit Unpaused(); }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "BAD_OWNER");
        address old = owner;
        owner = newOwner;
        emit OwnerChanged(old, newOwner);
    }

    function isActive(address user) external view returns (bool) {
        return accessUntil[user] >= block.timestamp;
    }

    function requiredTRXSun(uint256 months) public view returns (uint256) {
        require(months > 0, "BAD_MONTHS");
        // usdE6 = months * monthlyPriceUsdE6
        uint256 usdE6 = months * monthlyPriceUsdE6;

        // Convert USD micro units to SUN using trxUsdPriceE6.
        // requiredSun = ceil( (usdE6 * 1e6) / trxUsdPriceE6 )
        uint256 numerator = usdE6 * 1e6;
        uint256 denom = trxUsdPriceE6;
        return (numerator + denom - 1) / denom; // ceil
    }

    function subscribeTRX(uint256 months) external payable nonReentrant whenNotPaused {
        _subscribeFor(msg.sender, months);
    }

    function subscribeTRXFor(address beneficiary, uint256 months) external payable nonReentrant whenNotPaused {
        _subscribeFor(beneficiary, months);
    }

    function _subscribeFor(address beneficiary, uint256 months) internal {
        require(beneficiary != address(0), "BAD_BENEFICIARY");

        uint256 requiredSun = requiredTRXSun(months);
        require(msg.value == requiredSun, "BAD_TRX_AMOUNT");

        // effects first
        uint256 current = accessUntil[beneficiary];
        uint256 base = current > block.timestamp ? current : block.timestamp;
        uint256 newUntil = base + (months * 30 days);
        accessUntil[beneficiary] = newUntil;

        // interaction: forward to treasury vault
        (bool ok, ) = treasury.call{value: msg.value}("");
        require(ok, "TREASURY_FORWARD_FAIL");

        emit Subscribed(beneficiary, months, msg.value, newUntil);
    }
}