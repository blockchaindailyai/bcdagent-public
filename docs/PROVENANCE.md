# Source provenance and public preparation

This candidate derives from the owner's existing BCDAgent first-party source. The Python agent, API/worker, MCP server, frontend, scraper and contracts are actual implementation files, not illustrative replacements. The offline demo, test isolation runner and release documentation are new additions.

## Preparation changes

- Excluded vendor submodules, address reports, collected data, deployment configuration, audit evidence, operator runbooks, transaction-replay utilities and repository history.
- Replaced TRON address literals consistently with synthetic fixtures. Public token contract constants in ingestion code remain protocol references. Replaced a custom router-service host with an example.invalid host.
- Added synthetic test configuration and blank frontend deployment-address fields; no production config is shipped.
- Replaced automatic/manual/dual-approval example thresholds with 1.5%, 4% daily and 6% dual-approval values. These are demonstration constants, not recommended risk settings or statements about deployed policy.
- Reject non-finite/negative policy inputs, reject invalid numeric policy configuration, and halt reconciliation on invalid drift values. Added regression tests.
- Constrained extension-package output to a child of the build directory before removing/recreating it.
- Updated tests for public thresholds, portable Windows cleanup/path handling and a relative future swap deadline.
- Corrected legacy Parquet discovery so date directories alone do not imply bucket partitioning, missing critical columns remain detectable, and absent bucket values do not discard legacy records.
- Declared the existing router script's ethers v6 dependency in the root package/lockfile.

Simulation does not prove transaction execution. Included transport and signing adapters require a separate operational/security review before any deployment. No live transactions or deployment changes were made during preparation.

## Existing license and attribution evidence

The source root and intelligence API package manifests declare ISC. All six Solidity source files contain MIT SPDX identifiers. These declarations are preserved verbatim. No new LICENSE text is supplied and no contract notice is replaced by the package-level declaration.

Dependencies are obtained from npm/Python distributions or optional public upstream repositories, rather than copied from vendor checkouts. Keep their license and attribution files with installed or redistributed packages. The raw third-party testnet ABI/source dump was excluded rather than silently treated as first-party code.
