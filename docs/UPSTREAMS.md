# Optional upstream dependencies

Vendor repositories are not bundled and are not needed for the offline demo/tests or frontend build. The original project referenced the following revisions. These are optional integration references, not a claim that every integration is complete.

To inspect an optional dependency, first verify its upstream is public and licensed, then clone it into the listed path and check out the pinned commit. Keep its LICENSE, NOTICE, attribution and dependency license files intact. Do not copy a dependency into this project without reviewing those terms. For example:

```sh
git clone https://github.com/sunswap-finance/sunswap-sdk.git vendor/sunswap-sdk
git -C vendor/sunswap-sdk checkout eb7c923a738e04760fbf483bdca706c5e371a3a9
```

| Path | Configured upstream reference | Pinned revision | License at pinned revision |
| --- | --- | --- | --- |
| `vendor/bankofai/8004-sdk` | [bankofai/8004-sdk](https://github.com/bankofai/8004-sdk) | `3147da1e8b1e2a50c182bc8c5a0d8a954fce9b1a` | HTTP 404 |
| `vendor/bankofai/a2a-inspector` | [bankofai/a2a-inspector](https://github.com/bankofai/a2a-inspector) | `8098818f97c6b8554f1f83636508a9608842f5a0` | HTTP 404 |
| `vendor/bankofai/a2a-x402` | [bankofai/a2a-x402](https://github.com/bankofai/a2a-x402) | `1ed12acd0957bd258081cd1fb6f4bfab426d5e41` | HTTP 404 |
| `vendor/bankofai/agent-wallet` | [bankofai/agent-wallet](https://github.com/bankofai/agent-wallet) | `be401dd003868cc9d39f9fb28f3924492cfceeab` | HTTP 404 |
| `vendor/bankofai/docs` | [bankofai/docs](https://github.com/bankofai/docs) | `b09e5881927b5d12cacca8927fdb099564d5c54e` | HTTP 404 |
| `vendor/bankofai/evmbench` | [bankofai/evmbench](https://github.com/bankofai/evmbench) | `6e0a992d94dc66f7db63b17d1cdf68331157da71` | HTTP 404 |
| `vendor/bankofai/mcp-server-tron` | [bankofai/mcp-server-tron](https://github.com/bankofai/mcp-server-tron) | `6ab6628e7cff96f9b58c98ad5f481f56c744d925` | HTTP 404 |
| `vendor/bankofai/openclaw-extension` | [bankofai/openclaw-extension](https://github.com/bankofai/openclaw-extension) | `7a2de02bf3981898bd2460839a577e47a4a626de` | HTTP 404 |
| `vendor/bankofai/search-service` | [bankofai/search-service](https://github.com/bankofai/search-service) | `99d91ef141a6af78e5cfd3691623a29da67fbdbc` | HTTP 404 |
| `vendor/bankofai/skills` | [bankofai/skills](https://github.com/bankofai/skills) | `fd81a736f4966c6ecf9939dd25950e9434cd3fd4` | HTTP 404 |
| `vendor/bankofai/subgraph` | [bankofai/subgraph](https://github.com/bankofai/subgraph) | `efc40f3b844ecf4dc679551b058077f6aaac1349` | HTTP 404 |
| `vendor/bankofai/trc-8004-contracts` | [bankofai/trc-8004-contracts](https://github.com/bankofai/trc-8004-contracts) | `49c3608874fd69670378edf4ddcb3a1bd972fcc9` | HTTP 404 |
| `vendor/bankofai/x402` | [bankofai/x402](https://github.com/bankofai/x402) | `24b89cf44f52a5d0008f9fffe85d2798e4a3564b` | HTTP 404 |
| `vendor/bankofai/x402-contracts` | [bankofai/x402-contracts](https://github.com/bankofai/x402-contracts) | `378f6687061647bfc562cec5c79f2f4cc1dfb365` | HTTP 404 |
| `vendor/bankofai/x402-demo` | [bankofai/x402-demo](https://github.com/bankofai/x402-demo) | `e73ab97ad0aa8f715fd3fb968513dea2d66d7727` | HTTP 404 |
| `vendor/bankofai/x402-facilitator` | [bankofai/x402-facilitator](https://github.com/bankofai/x402-facilitator) | `c174667474d77b4ef20572783f375c8994539b95` | HTTP 404 |
| `vendor/sunswap-sdk` | [sunswap-finance/sunswap-sdk](https://github.com/sunswap-finance/sunswap-sdk) | `eb7c923a738e04760fbf483bdca706c5e371a3a9` | [MIT](https://github.com/sunswap-finance/sunswap-sdk/blob/eb7c923a738e04760fbf483bdca706c5e371a3a9/LICENSE) |

An unavailable or NOASSERTION license lookup is not a reuse permission; consult that upstream before redistribution. No upstream source was imported by this preparation. npm lockfiles retain package license metadata; installs obtain package license files from their published distributions.
