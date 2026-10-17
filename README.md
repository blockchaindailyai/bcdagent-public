# BCDAgent — simulation-first prototype

BCDAgent combines Python policy and execution planning, a TRON MCP transport, an intelligence API/worker, a React interface, a multi-source ingestion pipeline and Solidity treasury contracts. This is a sanitized first-party prototype source release candidate, not a newly invented mock application.

The agent defaults to simulation. Network execution paths remain experimental and require explicit configuration and opt-in. This project is not presented as a production custody system or a completed mainnet service.

## Start offline

Use Python 3.11+ in a virtual environment. From the repository root:

```sh
python -m pip install -r requirements-dev.txt
python -m examples.offline_policy_demo
python -B tools/test_offline.py
```

The demo uses actual governance and policy modules with synthetic inputs. It prints the manual-approval decision and reconciliation status without calling a signer, broadcaster or external service. The test runner strips runtime credentials and blocks network connections and child processes. Tests use fabricated fixtures and mocked transports.

## Components

| Directory | Purpose |
| --- | --- |
| `openclaw_agent/capabilities/` | Policy, governance, execution planning, configuration, verification, audit-record modeling, rollout and operations logic |
| `openclaw_agent/turnkey/` | Prototype CLI/runtime, signing/transport adapters, indexing and reconciliation |
| `mcp-server-tron/` | Node transport and local indexer/search service |
| `bcd-intel-api/` | Intelligence API and worker with JWT regression tests |
| `bcd-treasury-frontend/` | React/TypeScript UI; deployment addresses are blank placeholders |
| `scraper_bot/` | Ingestion sources, canonical schemas, batching and archive-query tools |
| `contracts/` | Treasury, budget, strategy, subscription and identity contracts |

## JavaScript checks

Use Node 22.12+ and npm. Install from lockfiles with lifecycle scripts disabled:

```sh
npm ci --ignore-scripts
npm --prefix mcp-server-tron ci --ignore-scripts
npm --prefix bcd-treasury-frontend ci --ignore-scripts
node --test bcd-intel-api/test/worker-auth.test.cjs mcp-server-tron/test/server.test.js
npm --prefix bcd-treasury-frontend run build
```

The worker tests do not require the full Express service dependency install. To develop that service separately, use `npm --prefix bcd-intel-api ci --ignore-scripts`. No service is started by the offline Python demo.

## Public configuration and provenance

`openclaw_agent/config/testnet_turnkey.json` is a newly constructed **synthetic fixture**, with example.invalid RPC endpoints and synthetic addresses. It is not a deployment configuration. Frontend contract addresses must be configured separately. Example governance thresholds are intentionally not deployed policy settings.

See [provenance and changes](docs/PROVENANCE.md) and [optional upstream dependencies](docs/UPSTREAMS.md). No vendor checkout, runtime data, audit evidence, private configuration, keys or repository history is bundled.

## Validation and limitations

Validation during preparation: 321 Python tests and 35 subtests passed with network/subprocess access blocked; 14 Node tests passed against in-process or loopback fixtures. The frontend TypeScript/Vite build completed, with a warning that the preparation machine's Node 20.11.1 is below Vite's supported minimum. Use Node 22.12+ for a supported setup.

The frontend build checks compilation, not wallet behavior or a deployed application. Solidity contracts have not been compiled or audited during this preparation. Smart-router, signer and chain-broadcast paths have not been tested against live networks.

Root package metadata retains its existing ISC declaration; Solidity files retain their existing MIT SPDX notices. No new blanket license or ownership claim is introduced. Third-party packages retain their own licenses.
