# Consensus design of `verify_milestone`

> GrantFlow reuses the verification code of my accepted MilestoneGrant contract **unchanged**: the constants and
> pure helpers between the `REUSED BEGIN` / `REUSED END` markers in `contracts/grantflow.py` (`_fetch_evidence`,
> `_evaluate`, `_evidence_matches`, `_http_error`, the parsers and `EVIDENCE_KEYS`) are byte-identical to lines
> 49-521 of `milestone_grant.py` (checked by `tests/direct/test_reuse.py`). `verify_milestone` keeps the same
> leader/validator pair and post-consensus checks, with these GrantFlow changes: the payee is the grant's **assigned
> builder** (stored on-chain, not supplied by the caller), the method refuses to run when no builder is assigned, and
> it updates the PASS / FAIL / DEFER counters and the grant status. The rest of this document is
> carried over from MilestoneGrant, with the GrantFlow-specific notes marked.

## The problem

A milestone pays out based on GitHub state: a release, CI results, a merged PR, and commit activity. A smart contract cannot
read GitHub on its own. On an ordinary chain, someone has to report the facts, either an oracle network or a
keeper. Whoever reports controls the payout.

On GenLayer, a transaction can run a **non-deterministic block** (`gl.vm.run_nondet`). The leader executes it, and
validators decide whether they accept the leader's result. This contract uses a **custom leader/validator pair**, not an
LLM and not a generic comparative equivalence prompt:

```python
def leader_fn() -> dict:
    return _fetch_evidence(gid, idx, crit)

def validator_fn(leaders_res) -> bool:
    if not isinstance(leaders_res, gl.vm.Return):
        return False                       # leader error -> reject
    mine = _fetch_evidence(gid, idx, crit) # independent fetch by this validator
    return _evidence_matches(leaders_res.calldata, mine, crit)

ev = gl.vm.run_nondet(leader_fn, validator_fn)
```

## Why consensus is still needed when no LLM is involved

The verdict function `_evaluate` is fully deterministic. The **inputs** are not: they come from an external HTTP API.
If only the leader fetched GitHub, the leader would be a single trusted oracle. It could report a release that does not
exist, a green CI that is red, or a payout that does not follow from the data. Here, every validator performs its own
HTTP requests to `api.github.com` and recomputes the evidence. A leader's report becomes the transaction result only
if a majority of validators observe the **same settlement-driving facts** and the **same verdict and payout**.
So lying requires a majority of validators to collude, not just one party.

## Why derived fields are compared, not raw responses

Two honest fetches of the same GitHub endpoint are often not byte-identical:

* volatile fields change (`updated_at`, `download_count`, reactions, run `started_at`, rate-limit headers)
* list order can differ (check runs)
* one node may be rate limited while another is not

Comparing raw bodies would make honest validators disagree all the time. Comparing only the final verdict would be too
weak: a leader could reach the right verdict for the wrong reasons, or bind a different commit. So the leader and
validators both reduce each response to a fixed set of **derived fields**. These are exactly the values that drive
settlement, normalized: sorted lists, ISO timestamps converted to unix seconds, booleans coerced strictly. Then all of
them are compared **exactly**.

### What `_evidence_matches` requires

1. The leader's result is a dict whose key set is exactly `EVIDENCE_KEYS` (35 keys). No extra or missing keys.
2. `canonical_json(leader) == canonical_json(mine)`. The comparison is strict and typed (`true` ≠ `1`, `"0"` ≠ `0`)
   and covers:
   * identity: `grant_id`, `milestone`, `owner`, `repo`, `tag`, `deadline`, `checks_mode`, `pr_number`, `commit_author`
   * release: HTTP status, `tag_name`, `draft`, `published_at` and its unix seconds
   * resolved commit SHA of the tag
   * every check run as `[name, status, conclusion]` (sorted), plus `total_count`
   * combined status state, `total_count`, every `[context, state]` (sorted)
   * PR: HTTP status, `state`, `merged`, `merged_at` and its unix seconds, base branch
   * commit count
   * `fetch_error` (which request failed and how), `verdict`, `reason`, and the exact `payout` in wei
3. `_evaluate(leader_facts) == [leader.verdict, leader.reason, leader.payout]`. The leader's verdict must follow from
   the leader's own facts. This is implied by (2) when the validator is honest, and it is kept as an explicit check.

No tolerance is needed. Unlike measurements, GitHub facts are discrete: a SHA, a name, a conclusion, a timestamp
string, a count.

### After consensus (deterministic, every node)

`verify_milestone` re-checks the agreed evidence before touching money:

* the key set is exact, and the identity fields equal the stored milestone (`_blank_evidence` of the stored criteria)
* `_evaluate(ev, stored_criteria)` reproduces `verdict`, `reason`, `payout`
* on PASS: `payout == amount` and `paid_amount == 0`. Then status becomes `PAID` and the amount is transferred once.
  Integer arithmetic only.
* *(GrantFlow)* the recipient is `grant.builder` read from storage at that moment; it is recorded as `paid_to` on
  the milestone. A builder cannot resign while one of its milestones is `CLAIMED`, so the payee of a claimed
  milestone cannot change between claim and verification.
* *(GrantFlow)* `grant_id` is one of the bound evidence fields, so evidence produced for one grant cannot settle
  another grant with identical criteria (`tests/direct/test_lifecycle.py::test_evidence_binds_grant_id`).

## Errors are deterministic, too

Every non-200 response is turned into a reason code by `_http_error`:

| Response | Reason |
|---|---|
| 429, or 403 whose body mentions "rate limit" | `GITHUB_RATE_LIMIT:<endpoint>` |
| other 403 | `GITHUB_HTTP_403:<endpoint>` |
| 404 | `GITHUB_NOT_FOUND:<endpoint>` |
| other status | `GITHUB_HTTP_<code>:<endpoint>` |
| request raised | `GITHUB_UNREACHABLE:<endpoint>` |
| 200 with malformed JSON or shape | `GITHUB_BAD_RESPONSE:<endpoint>` |

Any of these makes the verdict `DEFER`: no money moves and the milestone stays `CLAIMED`. Fetching stops at the first
error, which saves rate limit. Because the reason is bound, a leader cannot turn "I was rate limited" into a FAIL,
and it cannot hide an error behind a PASS.

## What happens when nodes see different data

* **Leader and validators see the same thing** (the normal case): agreement and the verdict is applied.
* **The data changes between fetches** (e.g. a check is re-run mid-round), or **only some nodes are rate limited**:
  validators vote disagree and the round fails to reach agreement. GenLayer then rotates the leader or leaves the
  transaction undetermined. No state changes, so no money moves either way. Anyone can call `verify_milestone`
  again within `deadline + grace`.
* **A dishonest leader** changes any bound field (SHA, a conclusion, `published_at`, the PR base, the count, the
  verdict, the payout): honest validators vote disagree. The tests tamper with each of the 35 keys and with nested
  list elements, and every case is rejected.

## What was observed live (GrantFlow deployment)

On GenLayer Studionet, contract `0x72eEF6Ea426dCaBcD15F0A7B8864900cc0480940` (see [docs/LIVE_RUN.md](docs/LIVE_RUN.md)),
three `verify_milestone` transactions finalized with `MAJORITY_AGREE` (3 validators `agree`, 2 `idle` in each):

| Grant / milestone | Repository | Verdict |
|---|---|---|
| #1 / 0 (release + 6 required check runs + combined status + PR #5924 into `stable` + ≥10 commits by `davidism`) | `pallets/flask` | `PASS`, 1 GEN to the assigned builder |
| #1 / 1 (every check run must pass) | `pallets/flask` | `FAIL` `CHECK_FAILED:Development Versions`, no transfer |
| #3 / 0 (every check run must pass), sent from the web UI | `psf/requests` | `PASS`, 0.5 GEN to the assigned builder |

No disagreement or DEFER case was observed live. Those paths are covered by the direct-mode tests in
`tests/direct/test_validator.py`, `tests/direct/test_verify.py` and `tests/direct/test_lifecycle.py`.
