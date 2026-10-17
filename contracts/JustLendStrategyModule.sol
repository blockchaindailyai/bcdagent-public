// SPDX-License-Identifier: MIT
pragma solidity ^0.8.6;

interface ITRC20Like {
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address owner) external view returns (uint256);
}

interface ITreasuryVaultModule {
    function spendTRX(address payable to, uint256 amount) external;
    function spendToken(address token, address to, uint256 amount) external;
}

/**
 * JustLendStrategyModule
 *
 * Pattern A strategy executor that sits behind TreasuryVaultV2 MODULE_ROLE.
 * The module can only call allowlisted targets + function selectors and enforces
 * autonomous borrow-pair constraints from OpenClaw governance policy.
 */
contract JustLendStrategyModule {
    bytes4 private constant TRC20_TRANSFER_SELECTOR = 0xa9059cbb;

    uint8 public constant TOKEN_TRANSFER_MODE_STRICT = 0;
    uint8 public constant TOKEN_TRANSFER_MODE_BALANCE_DELTA = 1;

    address public admin;
    address public operator;
    ITreasuryVaultModule public treasury;

    bool public paused;

    mapping(address => bool) public allowedAssets;
    address[] public trackedAssets;
    mapping(address => uint256) private trackedAssetIndexPlusOne;
    mapping(address => bool) public allowedTargets;
    mapping(address => uint8) public tokenTransferMode;
    bool public emitTransferDiagnostics;
    mapping(bytes4 => bool) public allowedSelectors;
    mapping(address => mapping(address => bool)) public allowedBorrowPairs;

    event OperatorChanged(address indexed oldOperator, address indexed newOperator);
    event AdminChanged(address indexed oldAdmin, address indexed newAdmin);
    event TreasuryChanged(address indexed oldTreasury, address indexed newTreasury);
    event PauseChanged(bool paused);
    event AssetAllowlistUpdated(address indexed asset, bool allowed);
    event TargetAllowlistUpdated(address indexed target, bool allowed);
    event SelectorAllowlistUpdated(bytes4 indexed selector, bool allowed);
    event BorrowPairAllowlistUpdated(address indexed collateralAsset, address indexed borrowAsset, bool allowed);
    event AssetSweptToTreasury(address indexed asset, uint256 amount);
    event TRXSweptToTreasury(uint256 amount);
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

    event StrategyCallExecuted(
        bytes32 indexed intentId,
        bytes32 indexed planId,
        address indexed target,
        bytes4 selector,
        address asset,
        uint256 amount,
        uint256 callValue,
        bytes result
    );

    bytes4 private constant CTOKEN_MINT_SELECTOR = bytes4(keccak256("mint()"));
    bytes4 private constant CTOKEN_REDEEM_SELECTOR = bytes4(keccak256("redeem(uint256)"));
    bytes4 private constant CTOKEN_REDEEM_UNDERLYING_SELECTOR = bytes4(keccak256("redeemUnderlying(uint256)"));
    bytes4 private constant CTOKEN_BORROW_SELECTOR = bytes4(keccak256("borrow(uint256)"));
    bytes4 private constant CTOKEN_REPAY_BORROW_SELECTOR = bytes4(keccak256("repayBorrow(uint256)"));
    bytes4 private constant CTOKEN_REPAY_BORROW_BEHALF_SELECTOR = bytes4(keccak256("repayBorrowBehalf(address,uint256)"));
    bytes4 private constant CTOKEN_LIQUIDATE_BORROW_SELECTOR = bytes4(keccak256("liquidateBorrow(address,uint256,address)"));
    bytes4 private constant CTOKEN_ADD_RESERVES_SELECTOR = bytes4(keccak256("_addReserves(uint256)"));

    enum SelectorReturnMode {
        NONE,
        BOOL_TRUE,
        UINT_ZERO
    }

    mapping(bytes4 => SelectorReturnMode) public selectorReturnMode;

    event SelectorReturnModeUpdated(bytes4 indexed selector, SelectorReturnMode mode);

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

    constructor(address treasury_, address admin_, address operator_) {
        require(treasury_ != address(0), "BAD_TREASURY");
        require(admin_ != address(0), "BAD_ADMIN");
        treasury = ITreasuryVaultModule(treasury_);
        admin = admin_;
        operator = operator_;

        _setSelectorReturnMode(CTOKEN_MINT_SELECTOR, SelectorReturnMode.UINT_ZERO);
        _setSelectorReturnMode(CTOKEN_REDEEM_SELECTOR, SelectorReturnMode.UINT_ZERO);
        _setSelectorReturnMode(CTOKEN_REDEEM_UNDERLYING_SELECTOR, SelectorReturnMode.UINT_ZERO);
        _setSelectorReturnMode(CTOKEN_BORROW_SELECTOR, SelectorReturnMode.UINT_ZERO);
        _setSelectorReturnMode(CTOKEN_REPAY_BORROW_SELECTOR, SelectorReturnMode.UINT_ZERO);
        _setSelectorReturnMode(CTOKEN_REPAY_BORROW_BEHALF_SELECTOR, SelectorReturnMode.UINT_ZERO);
        _setSelectorReturnMode(CTOKEN_LIQUIDATE_BORROW_SELECTOR, SelectorReturnMode.UINT_ZERO);
        _setSelectorReturnMode(CTOKEN_ADD_RESERVES_SELECTOR, SelectorReturnMode.UINT_ZERO);
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
        treasury = ITreasuryVaultModule(newTreasury);
        emit TreasuryChanged(oldTreasury, newTreasury);
    }

    function setPaused(bool value) external onlyAdmin {
        if (paused != value) {
            paused = value;
            emit PauseChanged(value);
        }
    }

    function setAllowedAsset(address asset, bool allowed) external onlyAdmin {
        require(asset != address(0), "BAD_ASSET");
        if (allowed && trackedAssetIndexPlusOne[asset] == 0) {
            trackedAssets.push(asset);
            trackedAssetIndexPlusOne[asset] = trackedAssets.length;
        }
        allowedAssets[asset] = allowed;
        emit AssetAllowlistUpdated(asset, allowed);
    }

    function setAllowedTarget(address target, bool allowed) external onlyAdmin {
        require(target != address(0), "BAD_TARGET");
        allowedTargets[target] = allowed;
        emit TargetAllowlistUpdated(target, allowed);
    }

    function setAllowedSelector(bytes4 selector, bool allowed) external onlyAdmin {
        allowedSelectors[selector] = allowed;
        emit SelectorAllowlistUpdated(selector, allowed);
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

    function setSelectorReturnMode(bytes4 selector, SelectorReturnMode mode) external onlyAdmin {
        _setSelectorReturnMode(selector, mode);
    }

    function setAllowedBorrowPair(address collateralAsset, address borrowAsset, bool allowed) external onlyAdmin {
        require(collateralAsset != address(0), "BAD_COLLATERAL");
        require(borrowAsset != address(0), "BAD_BORROW");
        allowedBorrowPairs[collateralAsset][borrowAsset] = allowed;
        emit BorrowPairAllowlistUpdated(collateralAsset, borrowAsset, allowed);
    }

    function executeCallWithToken(
        bytes32 intentId,
        bytes32 planId,
        address asset,
        uint256 amount,
        address target,
        bytes calldata callData
    ) external onlyOperator whenNotPaused returns (bytes memory) {
        require(allowedAssets[asset], "ASSET_NOT_ALLOWED");
        require(allowedTargets[target], "TARGET_NOT_ALLOWED");
        require(target.code.length > 0, "TARGET_NOT_CONTRACT");
        bytes4 selector = _selectorFromCalldata(callData);
        require(allowedSelectors[selector], "SELECTOR_NOT_ALLOWED");

        if (amount > 0) {
            require(asset.code.length > 0, "ASSET_NOT_CONTRACT");
            treasury.spendToken(asset, address(this), amount);
            _forceApprove(asset, target, amount);
        }

        bytes memory result = _callTarget(target, 0, callData);
        _enforceSelectorReturnMode(selector, result);

        if (amount > 0) {
            _clearApproval(asset, target);
        }

        _sweepAllowedAssetsToTreasury();
        _sweepTRXToTreasury();

        emit StrategyCallExecuted(intentId, planId, target, selector, asset, amount, 0, result);
        return result;
    }

    function executeCallWithTRX(
        bytes32 intentId,
        bytes32 planId,
        uint256 amount,
        address payable target,
        bytes calldata callData
    ) external onlyOperator whenNotPaused returns (bytes memory) {
        require(allowedTargets[target], "TARGET_NOT_ALLOWED");
        require(target.code.length > 0, "TARGET_NOT_CONTRACT");
        bytes4 selector = _selectorFromCalldata(callData);
        require(allowedSelectors[selector], "SELECTOR_NOT_ALLOWED");

        if (amount > 0) {
            treasury.spendTRX(payable(address(this)), amount);
        }

        bytes memory result = _callTarget(target, amount, callData);
        _enforceSelectorReturnMode(selector, result);

        _sweepAllowedAssetsToTreasury();
        _sweepTRXToTreasury();

        emit StrategyCallExecuted(intentId, planId, target, selector, address(0), amount, amount, result);
        return result;
    }

    function executeBorrow(
        bytes32 intentId,
        bytes32 planId,
        address collateralAsset,
        address borrowAsset,
        address target,
        bytes calldata callData
    ) external onlyOperator whenNotPaused returns (bytes memory) {
        require(allowedAssets[collateralAsset], "COLLATERAL_NOT_ALLOWED");
        require(allowedAssets[borrowAsset], "BORROW_ASSET_NOT_ALLOWED");
        require(allowedBorrowPairs[collateralAsset][borrowAsset], "BORROW_PAIR_NOT_ALLOWED");
        require(allowedTargets[target], "TARGET_NOT_ALLOWED");
        require(target.code.length > 0, "TARGET_NOT_CONTRACT");

        bytes4 selector = _selectorFromCalldata(callData);
        require(allowedSelectors[selector], "SELECTOR_NOT_ALLOWED");

        bytes memory result = _callTarget(target, 0, callData);
        _enforceSelectorReturnMode(selector, result);


        _sweepAllowedAssetsToTreasury();
        _sweepTRXToTreasury();

        emit StrategyCallExecuted(intentId, planId, target, selector, borrowAsset, 0, 0, result);
        return result;
    }

    function sweepTokenToTreasury(address token) public onlyOperator {
        require(allowedAssets[token], "ASSET_NOT_ALLOWED");
        uint256 balance = ITRC20Like(token).balanceOf(address(this));
        if (balance == 0) {
            return;
        }

        _safeTokenTransfer(token, address(treasury), balance);
        emit AssetSweptToTreasury(token, balance);
    }

    function sweepTRXToTreasury() public onlyOperator {
        uint256 trxBalance = address(this).balance;
        if (trxBalance == 0) {
            return;
        }

        (bool success, ) = payable(address(treasury)).call{value: trxBalance}("");
        require(success, "SWEEP_TRX_FAILED");
        emit TRXSweptToTreasury(trxBalance);
    }

    function sweepAllIdleToTreasury() external onlyOperator {
        _sweepAllowedAssetsToTreasury();
        sweepTRXToTreasury();
    }

    function _selectorFromCalldata(bytes calldata callData) internal pure returns (bytes4 selector) {
        require(callData.length >= 4, "BAD_CALLDATA");
        assembly {
            selector := calldataload(callData.offset)
        }
    }

    function _enforceSelectorReturnMode(bytes4 selector, bytes memory result) internal view {
        SelectorReturnMode mode = selectorReturnMode[selector];
        if (mode == SelectorReturnMode.NONE) {
            return;
        }

        require(result.length >= 32, "BAD_RETURN_DATA");

        uint256 value;
        assembly {
            value := mload(add(result, 32))
        }

        if (mode == SelectorReturnMode.BOOL_TRUE) {
            require(value == 1, "PROTOCOL_BOOL_FALSE");
            return;
        }

        require(value == 0, "PROTOCOL_ERROR_CODE");
    }

    function _setSelectorReturnMode(bytes4 selector, SelectorReturnMode mode) internal {
        selectorReturnMode[selector] = mode;
        emit SelectorReturnModeUpdated(selector, mode);
    }

    function _forceApprove(address asset, address target, uint256 amount) internal {
        bool cleared = ITRC20Like(asset).approve(target, 0);
        require(cleared, "APPROVE_RESET_FAILED");
        bool approved = ITRC20Like(asset).approve(target, amount);
        require(approved, "APPROVE_FAILED");
    }

    function _clearApproval(address asset, address target) internal {
        bool cleared = ITRC20Like(asset).approve(target, 0);
        require(cleared, "APPROVE_CLEAR_FAILED");
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

    function _callTarget(address target, uint256 callValue, bytes calldata callData) internal returns (bytes memory) {
        (bool success, bytes memory result) = target.call{value: callValue}(callData);
        if (!success) {
            if (result.length > 0) {
                assembly {
                    revert(add(result, 32), mload(result))
                }
            }
            revert("TARGET_CALL_FAILED");
        }
        return result;
    }

    function _sweepAllowedAssetsToTreasury() internal {
        uint256 length = trackedAssets.length;
        for (uint256 i = 0; i < length; i++) {
            address asset = trackedAssets[i];
            if (!allowedAssets[asset]) {
                continue;
            }

            sweepTokenToTreasury(asset);
        }
    }

    function _sweepTRXToTreasury() internal {
        sweepTRXToTreasury();
    }
}
