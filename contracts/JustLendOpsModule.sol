// SPDX-License-Identifier: MIT
pragma solidity ^0.8.6;

interface ITRC20Like {
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address owner) external view returns (uint256);
}

interface ITreasuryVaultLike {
    function spendTRX(address payable to, uint256 amount) external;
    function spendToken(address token, address to, uint256 amount) external;
}

interface IJustLendMarketLike {
    function mint() external payable;
    function mint(uint256 mintAmount) external returns (uint256);
    function redeem(uint256 redeemTokens) external returns (uint256);
    function redeemUnderlying(uint256 redeemAmount) external returns (uint256);
    function borrow(uint256 borrowAmount) external returns (uint256);
    function borrowBalanceCurrent(address account) external returns (uint256);
    function balanceOf(address owner) external view returns (uint256);
    function getAccountSnapshot(address account) external view returns (uint256, uint256, uint256, uint256);
}

interface IJustLendComptrollerLike {
    function enterMarkets(address[] calldata jTokens) external returns (uint256[] memory);
    function exitMarket(address jTokenAddress) external returns (uint256);
}

/**
 * JustLendOpsModule
 *
 * Purpose-built module that can hold collateral and borrow positions directly,
 * while still integrating with TreasuryVaultV2 MODULE_ROLE for controlled funding.
 * This keeps protocol-specific risk isolated from the main treasury vault.
 */
contract JustLendOpsModule {
    bytes4 private constant TRC20_TRANSFER_SELECTOR = 0xa9059cbb;

    uint8 public constant TOKEN_TRANSFER_MODE_STRICT = 0;
    uint8 public constant TOKEN_TRANSFER_MODE_BALANCE_DELTA = 1;

    address public admin;
    address public operator;
    ITreasuryVaultLike public treasury;
    IJustLendComptrollerLike public immutable comptroller;

    bool public paused;

    mapping(address => bool) public allowedMarkets;
    mapping(address => bool) public allowedAssets;
    mapping(address => uint8) public tokenTransferMode;
    bool public emitTransferDiagnostics;
    address[] public trackedAssets;
    mapping(address => uint256) private trackedAssetIndexPlusOne;

    event AdminChanged(address indexed oldAdmin, address indexed newAdmin);
    event TreasuryChanged(address indexed oldTreasury, address indexed newTreasury);
    event OperatorChanged(address indexed oldOperator, address indexed newOperator);
    event PauseChanged(bool paused);

    event MarketAllowlistUpdated(address indexed market, bool allowed);
    event AssetAllowlistUpdated(address indexed asset, bool allowed);

    event EnterMarketResult(address indexed market, uint256 errorCode);
    event ExitMarketResult(address indexed market, uint256 errorCode);

    event Supplied(address indexed market, uint256 amountSun);
    event SuppliedToken(address indexed market, address indexed asset, uint256 amount, uint256 protocolCode);
    event Redeemed(address indexed market, uint256 amount, bool byUnderlying, uint256 protocolCode);
    event Borrowed(address indexed market, uint256 amountSun, uint256 protocolCode);
    event Repaid(address indexed market, uint256 amountSun, uint256 protocolCode);

    event SweptTRX(uint256 amountSun);
    event SweptToken(address indexed token, uint256 amount);
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

    modifier onlyAdmin() {
        require(msg.sender == admin, "NOT_ADMIN");
        _;
    }

    modifier onlyOperator() {
        require(msg.sender == operator || msg.sender == admin, "NOT_OPERATOR");
        _;
    }

    modifier whenNotPaused() {
        require(!paused, "PAUSED");
        _;
    }

    constructor(address treasury_, address comptroller_, address admin_, address operator_) {
        require(treasury_ != address(0), "BAD_TREASURY");
        require(comptroller_ != address(0), "BAD_COMPTROLLER");
        require(admin_ != address(0), "BAD_ADMIN");
        require(operator_ != address(0), "BAD_OPERATOR");

        treasury = ITreasuryVaultLike(treasury_);
        comptroller = IJustLendComptrollerLike(comptroller_);
        admin = admin_;
        operator = operator_;
    }

    receive() external payable {}

    function setAdmin(address newAdmin) external onlyAdmin {
        require(newAdmin != address(0), "BAD_ADMIN");
        address old = admin;
        admin = newAdmin;
        emit AdminChanged(old, newAdmin);
    }

    function setOperator(address newOperator) external onlyAdmin {
        require(newOperator != address(0), "BAD_OPERATOR");
        address old = operator;
        operator = newOperator;
        emit OperatorChanged(old, newOperator);
    }


    function setTreasury(address newTreasury) external onlyAdmin {
        require(paused, "NOT_PAUSED");
        require(newTreasury != address(0), "BAD_TREASURY");
        address oldTreasury = address(treasury);
        require(newTreasury != oldTreasury, "TREASURY_UNCHANGED");
        treasury = ITreasuryVaultLike(newTreasury);
        emit TreasuryChanged(oldTreasury, newTreasury);
    }

    function setPaused(bool paused_) external onlyAdmin {
        paused = paused_;
        emit PauseChanged(paused_);
    }

    function setAllowedMarket(address market, bool allowed) external onlyAdmin {
        require(market != address(0), "BAD_MARKET");
        allowedMarkets[market] = allowed;
        emit MarketAllowlistUpdated(market, allowed);
    }

    function setAllowedAsset(address asset, bool allowed) external onlyAdmin {
        require(asset != address(0), "BAD_ASSET");
        allowedAssets[asset] = allowed;

        if (trackedAssetIndexPlusOne[asset] == 0) {
            trackedAssets.push(asset);
            trackedAssetIndexPlusOne[asset] = trackedAssets.length;
        }

        emit AssetAllowlistUpdated(asset, allowed);
    }

    function setEmitTransferDiagnostics(bool enabled) external onlyAdmin {
        emitTransferDiagnostics = enabled;
    }

    function setTokenTransferMode(address token, uint8 mode) external onlyAdmin {
        require(token != address(0), "BAD_ASSET");
        require(mode == TOKEN_TRANSFER_MODE_STRICT || mode == TOKEN_TRANSFER_MODE_BALANCE_DELTA, "BAD_MODE");
        tokenTransferMode[token] = mode;
        emit TokenTransferModeUpdated(token, mode);
    }

    function enterMarket(address market) external onlyOperator whenNotPaused returns (uint256) {
        require(allowedMarkets[market], "MARKET_NOT_ALLOWED");
        address[] memory markets = new address[](1);
        markets[0] = market;
        uint256[] memory errors = comptroller.enterMarkets(markets);
        uint256 errorCode = errors.length > 0 ? errors[0] : 1;
        emit EnterMarketResult(market, errorCode);
        return errorCode;
    }

    function exitMarket(address market) external onlyOperator whenNotPaused returns (uint256) {
        require(allowedMarkets[market], "MARKET_NOT_ALLOWED");
        uint256 errorCode = comptroller.exitMarket(market);
        emit ExitMarketResult(market, errorCode);
        return errorCode;
    }

    function supplyTRX(address market, uint256 amountSun) external onlyOperator whenNotPaused returns (bool) {
        require(allowedMarkets[market], "MARKET_NOT_ALLOWED");
        require(amountSun > 0, "BAD_AMOUNT");

        treasury.spendTRX(payable(address(this)), amountSun);
        IJustLendMarketLike(market).mint{value: amountSun}();
        emit Supplied(market, amountSun);
        return true;
    }


    function supplyToken(address market, address underlyingAsset, uint256 amount) external onlyOperator whenNotPaused returns (uint256) {
        require(allowedMarkets[market], "MARKET_NOT_ALLOWED");
        require(allowedAssets[underlyingAsset], "ASSET_NOT_ALLOWED");
        require(amount > 0, "BAD_AMOUNT");

        treasury.spendToken(underlyingAsset, address(this), amount);
        bool cleared = ITRC20Like(underlyingAsset).approve(market, 0);
        require(cleared, "APPROVE_RESET_FAILED");
        bool approved = ITRC20Like(underlyingAsset).approve(market, amount);
        require(approved, "APPROVE_FAILED");

        uint256 code = IJustLendMarketLike(market).mint(amount);

        bool reset = ITRC20Like(underlyingAsset).approve(market, 0);
        require(reset, "APPROVE_CLEAR_FAILED");

        emit SuppliedToken(market, underlyingAsset, amount, code);
        return code;
    }

    function redeemJToken(address market, uint256 redeemTokens) external onlyOperator whenNotPaused returns (uint256) {
        require(allowedMarkets[market], "MARKET_NOT_ALLOWED");
        uint256 code = IJustLendMarketLike(market).redeem(redeemTokens);
        emit Redeemed(market, redeemTokens, false, code);
        return code;
    }

    function redeemUnderlying(address market, uint256 redeemAmountSun) external onlyOperator whenNotPaused returns (uint256) {
        require(allowedMarkets[market], "MARKET_NOT_ALLOWED");
        uint256 code = IJustLendMarketLike(market).redeemUnderlying(redeemAmountSun);
        emit Redeemed(market, redeemAmountSun, true, code);
        return code;
    }

    function borrow(address market, uint256 amountSun) external onlyOperator whenNotPaused returns (uint256) {
        require(allowedMarkets[market], "MARKET_NOT_ALLOWED");
        uint256 code = IJustLendMarketLike(market).borrow(amountSun);
        emit Borrowed(market, amountSun, code);
        return code;
    }

    function repayBorrow(address market, uint256 amountSun) external onlyOperator whenNotPaused returns (uint256) {
        require(allowedMarkets[market], "MARKET_NOT_ALLOWED");
        require(amountSun > 0, "BAD_AMOUNT");

        treasury.spendTRX(payable(address(this)), amountSun);
        (bool success, bytes memory result) = market.call{value: amountSun}(abi.encodeWithSignature("repayBorrow(uint256)", amountSun));
        require(success, "REPAY_CALL_FAILED");

        uint256 code = 0;
        if (result.length >= 32) {
            code = abi.decode(result, (uint256));
        }
        emit Repaid(market, amountSun, code);
        return code;
    }

    function repayBorrowToken(address market, address underlyingAsset, uint256 amount) external onlyOperator whenNotPaused returns (uint256) {
        require(allowedMarkets[market], "MARKET_NOT_ALLOWED");
        require(allowedAssets[underlyingAsset], "ASSET_NOT_ALLOWED");
        require(amount > 0, "BAD_AMOUNT");

        treasury.spendToken(underlyingAsset, address(this), amount);
        bool cleared = ITRC20Like(underlyingAsset).approve(market, 0);
        require(cleared, "APPROVE_RESET_FAILED");
        bool approved = ITRC20Like(underlyingAsset).approve(market, amount);
        require(approved, "APPROVE_FAILED");

        (bool success, bytes memory result) = market.call(abi.encodeWithSignature("repayBorrow(uint256)", amount));
        require(success, "REPAY_CALL_FAILED");

        bool reset = ITRC20Like(underlyingAsset).approve(market, 0);
        require(reset, "APPROVE_CLEAR_FAILED");

        uint256 code = 0;
        if (result.length >= 32) {
            code = abi.decode(result, (uint256));
        }
        emit Repaid(market, amount, code);
        return code;
    }

    function getAccountSnapshot(address market) external view returns (uint256, uint256, uint256, uint256) {
        return IJustLendMarketLike(market).getAccountSnapshot(address(this));
    }

    function borrowBalanceCurrent(address market) external returns (uint256) {
        return IJustLendMarketLike(market).borrowBalanceCurrent(address(this));
    }

    function jTokenBalance(address market) external view returns (uint256) {
        return IJustLendMarketLike(market).balanceOf(address(this));
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
            fromBefore = ITRC20Like(token).balanceOf(address(this));
            toBefore = ITRC20Like(token).balanceOf(to);
        }

        (bool success, bytes memory data) = token.call(abi.encodeWithSelector(TRC20_TRANSFER_SELECTOR, to, amount));
        require(success, "SWEEP_TOKEN_FAILED");

        // Compatibility-safe handling for legacy tokens (for example legacy USDT)
        // that do not return a boolean on transfer.
        if (data.length == 0) {
            if (emitTransferDiagnostics) {
                uint256 fromAfterCompat = ITRC20Like(token).balanceOf(address(this));
                uint256 toAfterCompat = ITRC20Like(token).balanceOf(to);
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

        require(data.length >= 32, "SWEEP_TOKEN_FAILED");
        bool decoded = abi.decode(data, (bool));
        require(decoded, "SWEEP_TOKEN_FAILED");

        if (emitTransferDiagnostics) {
            uint256 fromAfter = ITRC20Like(token).balanceOf(address(this));
            uint256 toAfter = ITRC20Like(token).balanceOf(to);
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
        uint256 fromBefore = ITRC20Like(token).balanceOf(address(this));
        uint256 toBefore = ITRC20Like(token).balanceOf(to);

        (bool success, bytes memory data) = token.call(abi.encodeWithSelector(TRC20_TRANSFER_SELECTOR, to, amount));
        require(success, "SWEEP_TOKEN_FAILED");

        bool decoded = true;
        if (data.length >= 32) {
            decoded = abi.decode(data, (bool));
        }

        uint256 fromAfter = ITRC20Like(token).balanceOf(address(this));
        uint256 toAfter = ITRC20Like(token).balanceOf(to);

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

        require(fromBefore >= amount, "SWEEP_TOKEN_FAILED");
        require(fromAfter + amount == fromBefore, "SWEEP_TOKEN_FAILED");
        require(toAfter >= toBefore, "SWEEP_TOKEN_FAILED");
        require(toAfter - toBefore == amount, "SWEEP_TOKEN_FAILED");
    }

    function sweepTRXToTreasury() public onlyOperator {
        uint256 amount = address(this).balance;
        if (amount == 0) {
            return;
        }
        (bool ok, ) = payable(address(treasury)).call{value: amount}("");
        require(ok, "SWEEP_TRX_FAILED");
        emit SweptTRX(amount);
    }

    function sweepTokenToTreasury(address token) public onlyOperator {
        require(allowedAssets[token], "ASSET_NOT_ALLOWED");
        uint256 amount = ITRC20Like(token).balanceOf(address(this));
        if (amount == 0) {
            return;
        }
        _safeTokenTransfer(token, address(treasury), amount);
        emit SweptToken(token, amount);
    }

    function sweepAllIdleToTreasury() external onlyOperator {
        uint256 length = trackedAssets.length;
        for (uint256 i = 0; i < length; i++) {
            address token = trackedAssets[i];
            if (!allowedAssets[token]) {
                continue;
            }
            sweepTokenToTreasury(token);
        }
        sweepTRXToTreasury();
    }
}
