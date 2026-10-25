# Live run on GenLayer Studionet (2026-10-25, UTC)

Everything below ran against the deployed contract on GenLayer Studionet
(`https://studio.genlayer.com/api`). All keys were throwaway Studionet keys created for this run; balances are
Studio test GEN with no real value. Every transaction listed here was checked afterwards with `getTransaction`:
all are **FINALIZED** with consensus **MAJORITY_AGREE** and leader execution `SUCCESS`.

| | |
|---|---|
| Contract | [`0x72eEF6Ea426dCaBcD15F0A7B8864900cc0480940`](https://explorer-studio.genlayer.com/address/0x72eEF6Ea426dCaBcD15F0A7B8864900cc0480940) |
| Source | `contracts/grantflow.py`, sha256 `8366ecc6c5c341a7a50da14974403b0736d4eab809980bc762637e0f065d0755` |
| On-chain code check | `npm run verify-code` → `MATCH` (the code returned by `getContractCode` is byte-identical to the file) |
| Deploy tx | `0xcd5ee6eade7e2967b6f705e8b50f48ff4bc5b41cd356026a88c19f4019791e04` · FINALIZED · 3 agree, 2 idle |

Accounts (all throwaway):

* Funder A, deployer (Node script): `0x01102148075847021438D74b5B40a6451C6f5fA1`
* Builder (Node script): `0xF3fd7A7dab36D9Ac771C1187Fb2253AD5260E49e`
* Funder B / verifier (Node script): `0x029E3a44F361Ca9C4b267788EECA748f7e87C343`
* Browser burner account (headless Chromium driving the built frontend, `tools/browser_check.py`): `0x38de13F75B93BFcfc55B5Dc5D30db28b7BB5A5d4`, acting as Funder C

## A. Node script (`frontend/scripts/e2e.mjs`, same client library as the UI)

| Step | Tx | Result |
|---|---|---|
| Funder A: `create_grant` "Flask 3.1.3 release (demo grant)", `pallets/flask`, open for applications, 2 milestones, deposit 1.5 GEN | `0xd28fec15f68b5e7b4b5cf6fdf9e52b1aa21421436e970e76b323ac683b791818` | grant `"1"` (OPEN) |
| Builder: `apply("1", "e2e-builder", …)` | `0x3121d371e3bbc4f6718af263de5be45044bf63f428b1f57cf13ea63511c07ad8` | application `"1/a0"` (PENDING) |
| Funder A: `assign_builder("1", builder)` | `0x4ea58d23d0e9c9659c2bac55fe42179a3c0eda921ecfbc1ac90f0f807cac88a6` | grant ACTIVE, application ACCEPTED |
| Builder: `claim_milestone("1", 0)` | `0xce2ab3b369e05827512ccef320eae0d5693d3549657952d6f92d63063eed11b2` | CLAIMED |
| Builder: `claim_milestone("1", 1)` | `0x4e27cbea1828f12174fc3f1536c6e5cc9a80dfa44a36fe34641f4c3f4c45c7f6` | CLAIMED |
| Funder B (anyone): `verify_milestone("1", 0)` | `0x1270f63e814fd4e4f8c2f7f7e2fa31abde4ec35c9d1449fd8caa5fbd96d9f259` | **PASS**, payout 1 GEN to the builder. 3 agree, 2 idle. Evidence: release `3.1.3` published 2026-02-19T05:01:30Z, tag commit `22d92470…`, the 6 required check runs `success`, combined status `success`, PR #5924 merged into `stable` at 2026-02-19T05:01:53Z, 15 commits by `davidism` in the range |
| Funder B (anyone): `verify_milestone("1", 1)` | `0xbae577da4a3764da5ab88ddcc5291afbd64517d5ccaf686cbae7cb99cb33bd79` | **FAIL** `CHECK_FAILED:Development Versions`, payout 0. 3 agree, 2 idle. Milestone re-opened (OPEN), 0.5 GEN stays locked |
| Funder B: `create_grant` "Requests v2.34.2 CI (demo, cancelled)", `psf/requests`, 0.25 GEN | `0xec739b3b55803b45a88ab73becd46de1a42b61be6154a7ce7288fdac11a43495` | grant `"2"` (OPEN) |
| Funder B: `cancel_grant("2")` | `0x900abe2923ba424cde1725c53dcd44113a6ebf38ce705ea5b18e30d543cfa8b6` | returned `250000000000000000`; grant CANCELLED, 0.25 GEN refunded |
| Funder A: `create_grant` "Click 8.5.0 release (demo, open for applications)", `pallets/click`, 0.2 GEN | `0xe8bedd2212b2d5d92302ae9ba5ad2c5f168a73fa0fac973730b4f39fe0d62fd9` | grant `"4"`, left OPEN |

## B. Browser (built `frontend/dist`, headless Chromium, `tools/browser_check.py`)

| UI action | Tx | Result |
|---|---|---|
| "+10 test GEN" (Studio faucet) | (faucet call, no contract tx) | balance 10 GEN shown |
| Funder tab → post "Requests v2.34.2 CI grant (UI demo)", `psf/requests`, builder address filled in, 1 milestone: tag `v2.34.2`, checks `check_runs`, 0.5 GEN | `0xb19a55c5808c371f9f14cba7ff6a63820a2fa12cb655526e0fe24412e4a6eca8` | grant `"3"`, ACTIVE (builder assigned at creation) |
| (Node) Builder: `claim_milestone("3", 0)` | `0x58a792bdeff92ed824690a1f15fa7bfd634173e0636ad65bf2b4d63b3ea7ba4d` | CLAIMED |
| Grants tab → grant #3 → **Verify on GitHub** | `0x5c56bb9b82df0ba1bf28992531f22388f101807bbab1ffb72e19e0d3bb2cd36a` | **PASS**, 0.5 GEN to the builder. 3 agree, 2 idle. Evidence: 66 latest check runs on `6e83187b…`, all passing. Grant CLOSED |
| Grants tab → grant #4 → Apply (GitHub login `grantflow-ui-demo`) | `0x3a0c71f7b53f185c7c5c3008eaf005f3a5e2f45913c46642f7df0106851d39aa` | application `"4/a0"` PENDING |
| Funder tab → "Fill example (pallets/flask 3.1.3)", amount 0.3 → post (open for applications) | `0xa4741675ec14982f2a62e87d1feb70ebf309052b5c89b2c8de9248c94a5d83ab` | grant `"5"` (OPEN) |
| (Node) Builder: `apply("5", …)` | `0xd067a1e9905e123a956701b69a763ab32cbd17c0b797816f9ed21865664fd59b` | PENDING |
| Grant #5 → **Assign** | `0x5b4f4f46b16090bcaa4a2b94b06480c82e957ae8f26e5b08bb3065c4754b5693` | grant ACTIVE, builder assigned |

Screenshots of these screens are in `docs/screenshots/` (`grants.png`, `grant-detail.png`, `funder-post.png`,
`verify-ui.png`, `apply-ui.png`, `funder-example.png`, `assign-ui.png`, `funder.png`, `builder.png`, `settings.png`).

## Balances after the run

Read with `getBalance` after finalization (`node scripts/e2e.mjs balances`, 2026-10-25T17:18:50Z):

| Account | Start | End | Explanation |
|---|---|---|---|
| Builder | 20 GEN (faucet) | 21.5 GEN | +1 GEN (grant #1 milestone 0) +0.5 GEN (grant #3) |
| Funder A | 100 GEN (faucet) | 98.3 GEN | −1.5 GEN (grant #1) −0.2 GEN (grant #4) |
| Funder B | 20 GEN (faucet) | 20 GEN | −0.25 GEN (grant #2) +0.25 GEN refund from `cancel_grant` |
| Contract | 0 | 1 GEN | locked: 0.5 (grant #1 milestone 1) + 0.2 (grant #4) + 0.3 (grant #5) |

`get_stats` at the end: 5 grants (1 OPEN, 2 ACTIVE, 1 CLOSED, 1 CANCELLED), 3 applications, 2.75 GEN deposited,
1.5 GEN paid, 0.25 GEN refunded, 1 GEN locked, verifications 2 PASS / 1 FAIL / 0 DEFER.

## Not done live (and why)

* **`reclaim_milestone`** needs the deadline plus grace to have passed. The demo milestones have deadlines in
  November–December 2026 (grant #1 milestone 1: deadline 2026-12-24T17:09:34Z, reclaimable after
  2026-12-31T17:09:34Z). It is covered by the direct-mode tests.
* **`resign` and `withdraw_application`** were not sent live. They are covered by direct-mode tests.
* **A DEFER verdict or a validator disagreement** was not observed live. Both are covered by direct-mode tests
  (HTTP 404 / 403 rate limit / 503 / malformed JSON / pending checks, and tampered leader evidence).
* **MetaMask.** The wallet path (`wallet_switchEthereumChain` / `wallet_addEthereumChain`, then `genlayer-js` with
  `provider: window.ethereum`; no Snap) is implemented but was not exercised here: a headless browser has no wallet.
  The burner-account path is the one tested end to end.
* The public Studio RPC sometimes dropped a read request from the browser (seen as a CORS / "Failed to fetch"
  error in the console). Reads are retried by the client library; one page load needed a reload.
