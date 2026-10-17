// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title TRC-8004 Identity Registry
/// @notice Stores DID-style TRC-8004 subject bindings for operational actors.
contract TRC8004IdentityRegistry {
    struct IdentityRecord {
        string subject;
        string role;
        uint64 issuedAt;
        uint64 expiresAt;
        bool revoked;
    }

    address public owner;
    mapping(address => bool) public registrars;
    mapping(address => IdentityRecord) private records;

    event RegistrarUpdated(address indexed registrar, bool enabled);
    event IdentityRegistered(
        address indexed actor,
        string subject,
        string role,
        uint64 issuedAt,
        uint64 expiresAt
    );
    event IdentityRevoked(address indexed actor, string reason);

    error Unauthorized();
    error InvalidActor();
    error InvalidSubject();
    error InvalidExpiry();

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    modifier onlyRegistrar() {
        if (!(msg.sender == owner || registrars[msg.sender])) revert Unauthorized();
        _;
    }

    constructor(address initialOwner) {
        owner = initialOwner == address(0) ? msg.sender : initialOwner;
    }

    function setRegistrar(address registrar, bool enabled) external onlyOwner {
        if (registrar == address(0)) revert InvalidActor();
        registrars[registrar] = enabled;
        emit RegistrarUpdated(registrar, enabled);
    }

    function registerIdentity(
        address actor,
        string calldata subject,
        string calldata role,
        uint64 expiresAt
    ) external onlyRegistrar {
        if (actor == address(0)) revert InvalidActor();
        if (bytes(subject).length == 0) revert InvalidSubject();
        if (expiresAt != 0 && expiresAt <= block.timestamp) revert InvalidExpiry();

        records[actor] = IdentityRecord({
            subject: subject,
            role: role,
            issuedAt: uint64(block.timestamp),
            expiresAt: expiresAt,
            revoked: false
        });

        emit IdentityRegistered(actor, subject, role, uint64(block.timestamp), expiresAt);
    }

    function revokeIdentity(address actor, string calldata reason) external onlyRegistrar {
        if (actor == address(0)) revert InvalidActor();
        IdentityRecord storage record = records[actor];
        if (bytes(record.subject).length == 0) revert InvalidSubject();

        record.revoked = true;
        emit IdentityRevoked(actor, reason);
    }

    function getIdentity(address actor) external view returns (IdentityRecord memory) {
        return records[actor];
    }

    function isIdentityActive(address actor) external view returns (bool) {
        IdentityRecord memory record = records[actor];
        if (bytes(record.subject).length == 0 || record.revoked) return false;
        if (record.expiresAt != 0 && record.expiresAt <= block.timestamp) return false;
        return true;
    }
}
