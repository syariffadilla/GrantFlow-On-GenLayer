# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
GrantFlow - an open board of GitHub-verified milestone grants.

Many funders post grants in one contract. Each grant is tied to one public
GitHub repository and has 1..20 milestones, each with its own GEN amount,
deadline and objective GitHub criteria (release tag published by the deadline,
CI check runs and/or combined commit status, a merged pull request into a given
base branch, a minimum commit count by a GitHub login in a time range).

The funder deposits the exact milestone total when posting the grant. Builders
apply; the funder assigns one applicant (or names a builder when posting).
The assigned builder claims a milestone, then anyone may call
verify_milestone(). Validators each read GitHub's public REST API and must
agree on every field of the evidence (gl.vm.run_nondet, custom leader /
validator). PASS pays the milestone amount to the assigned builder, once.
FAIL and DEFER move no money. After deadline + grace the funder can reclaim any
milestone that has not been paid. No LLM is used.

The GitHub evidence and verdict code (the block between the REUSED markers
below) is copied unchanged from the MilestoneGrant contract
(Studionet 0x8cD1213d653E5fADADa3B79feBb06fbbF4033211); tests/direct/test_reuse.py
checks its sha256. GrantFlow adds the multi-funder registry, applications,
assignment / resignation, grant cancellation before assignment, per-builder
payouts and paginated read views.

Units:
  * money: wei of native GEN (int / u256), integers only
  * time: unix seconds (UTC) as int
"""

import json
import datetime

from dataclasses import dataclass

from genlayer import *


# ==== REUSED BEGIN: copied unchanged from MilestoneGrant (contracts/milestone_grant.py) ====
# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

API = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "genlayer-milestone-grant",
}

MAX_MILESTONES = 20
MAX_REQUIRED_CHECKS = 20
MAX_MIN_COMMITS = 100  # commits are counted from one page of per_page=100
MIN_GRACE_DAYS = 1
MAX_GRACE_DAYS = 90
MAX_DEADLINE_AHEAD_S = 3 * 366 * 86400
DAY_S = 86400

CHECKS_NONE = "none"
CHECKS_RUNS = "check_runs"
CHECKS_STATUS = "status"
CHECKS_BOTH = "both"
CHECK_MODES = [CHECKS_NONE, CHECKS_RUNS, CHECKS_STATUS, CHECKS_BOTH]

# check-run conclusions that count as passing
PASSING_CONCLUSIONS = ["success", "neutral", "skipped"]

M_OPEN = "OPEN"
M_CLAIMED = "CLAIMED"
M_PAID = "PAID"
M_RECLAIMED = "RECLAIMED"
M_CANCELLED = "CANCELLED"

PASS = "PASS"
FAIL = "FAIL"
DEFER = "DEFER"
OK = "OK"

SPEC_KEYS = [
    "amount",
    "deadline",
    "tag",
    "checks",
    "required_checks",
    "not_before",
    "pr_number",
    "pr_base",
    "commit_author",
    "min_commits",
    "commits_since",
    "commits_until",
]

# Every key of the evidence dict. Validators require all of them to be identical.
EVIDENCE_KEYS = [
    "grant_id",
    "milestone",
    "owner",
    "repo",
    "tag",
    "deadline",
    "fetch_error",
    "release_http",
    "release_tag",
    "release_draft",
    "release_published_at",
    "release_published_ts",
    "commit_http",
    "commit_sha",
    "checks_mode",
    "check_runs_http",
    "check_runs_total",
    "check_runs",
    "status_http",
    "status_state",
    "status_total",
    "statuses",
    "pr_number",
    "pr_http",
    "pr_state",
    "pr_merged",
    "pr_merged_at",
    "pr_merged_ts",
    "pr_base",
    "commit_author",
    "commits_http",
    "commits_count",
    "verdict",
    "reason",
    "payout",
]

IDENTITY_KEYS = ["grant_id", "milestone", "owner", "repo", "tag", "deadline", "checks_mode", "pr_number", "commit_author"]

EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

NAME_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
LOGIN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-"


# --------------------------------------------------------------------------
# Pure helpers (deterministic; shared by leader, validator and contract)
# --------------------------------------------------------------------------


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_name(v, what: str, chars: str, max_len: int) -> str:
    if not isinstance(v, str) or len(v) == 0 or len(v) > max_len:
        raise gl.vm.UserError("Invalid " + what)
    for ch in v:
        if ch not in chars:
            raise gl.vm.UserError("Invalid " + what)
    if v in [".", ".."] or ".." in v:
        raise gl.vm.UserError("Invalid " + what)
    return v


def _ts_to_iso(ts: int) -> str:
    return (EPOCH + datetime.timedelta(seconds=ts)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_to_ts(s) -> int:
    """Strict 'YYYY-MM-DDTHH:MM:SSZ' (GitHub format) -> unix seconds. -1 if invalid."""
    if not isinstance(s, str) or len(s) != 20 or s[10] != "T" or s[19] != "Z":
        return -1
    try:
        d = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return -1
    if d.strftime("%Y-%m-%dT%H:%M:%SZ") != s:
        return -1
    return (d - EPOCH) // datetime.timedelta(seconds=1)


def _repo_base(owner: str, repo: str) -> str:
    return API + "/repos/" + owner + "/" + repo


def _url_release(owner: str, repo: str, tag: str) -> str:
    return _repo_base(owner, repo) + "/releases/tags/" + tag


def _url_commit(owner: str, repo: str, tag: str) -> str:
    return _repo_base(owner, repo) + "/commits/refs/tags/" + tag


def _url_check_runs(owner: str, repo: str, sha: str) -> str:
    return _repo_base(owner, repo) + "/commits/" + sha + "/check-runs?filter=latest&per_page=100"


def _url_status(owner: str, repo: str, sha: str) -> str:
    return _repo_base(owner, repo) + "/commits/" + sha + "/status?per_page=100"


def _url_pull(owner: str, repo: str, number: int) -> str:
    return _repo_base(owner, repo) + "/pulls/" + str(number)


def _url_commits(owner: str, repo: str, author: str, since_ts: int, until_ts: int) -> str:
    return (
        _repo_base(owner, repo)
        + "/commits?author=" + author
        + "&since=" + _ts_to_iso(since_ts)
        + "&until=" + _ts_to_iso(until_ts)
        + "&per_page=100"
    )


def _blank_evidence(grant_id: str, index: int, c: dict) -> dict:
    return {
        "grant_id": grant_id,
        "milestone": index,
        "owner": c["owner"],
        "repo": c["repo"],
        "tag": c["tag"],
        "deadline": c["deadline"],
        "fetch_error": "",
        "release_http": 0,
        "release_tag": "",
        "release_draft": False,
        "release_published_at": "",
        "release_published_ts": -1,
        "commit_http": 0,
        "commit_sha": "",
        "checks_mode": c["checks"],
        "check_runs_http": 0,
        "check_runs_total": -1,
        "check_runs": [],
        "status_http": 0,
        "status_state": "",
        "status_total": -1,
        "statuses": [],
        "pr_number": c["pr_number"],
        "pr_http": 0,
        "pr_state": "",
        "pr_merged": False,
        "pr_merged_at": "",
        "pr_merged_ts": -1,
        "pr_base": "",
        "commit_author": c["commit_author"],
        "commits_http": 0,
        "commits_count": -1,
        "verdict": "",
        "reason": "",
        "payout": 0,
    }


def _http_error(status: int, body, endpoint: str) -> str:
    """Deterministic classification of a non-200 GitHub response."""
    if status == 429:
        return "GITHUB_RATE_LIMIT:" + endpoint
    if status == 403:
        text = ""
        if body is not None:
            try:
                text = body.decode("utf-8").lower()
            except Exception:
                text = ""
        if "rate limit" in text:
            return "GITHUB_RATE_LIMIT:" + endpoint
        return "GITHUB_HTTP_403:" + endpoint
    if status == 404:
        return "GITHUB_NOT_FOUND:" + endpoint
    return "GITHUB_HTTP_" + str(status) + ":" + endpoint


def _get_json(url: str, endpoint: str) -> list:
    """Returns [http_status, parsed_json_or_None, error_reason]."""
    try:
        r = gl.nondet.web.get(url, headers=HEADERS)
    except Exception:
        return [0, None, "GITHUB_UNREACHABLE:" + endpoint]
    status = int(r.status)
    if status != 200:
        return [status, None, _http_error(status, r.body, endpoint)]
    try:
        data = json.loads(r.body.decode("utf-8"))
    except Exception:
        return [status, None, "GITHUB_BAD_RESPONSE:" + endpoint]
    return [status, data, ""]


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def _parse_release(ev: dict, data) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        return "GITHUB_BAD_RESPONSE:release"
    ev["release_tag"] = data["tag_name"]
    ev["release_draft"] = data.get("draft") is True
    ev["release_published_at"] = _str(data.get("published_at"))
    ev["release_published_ts"] = _iso_to_ts(data.get("published_at"))
    return ""


def _parse_commit(ev: dict, data) -> str:
    sha = data.get("sha") if isinstance(data, dict) else None
    if not isinstance(sha, str) or len(sha) != 40:
        return "GITHUB_BAD_RESPONSE:commit"
    for ch in sha:
        if ch not in "0123456789abcdef":
            return "GITHUB_BAD_RESPONSE:commit"
    ev["commit_sha"] = sha
    return ""


def _parse_check_runs(ev: dict, data) -> str:
    if not isinstance(data, dict) or not _is_int(data.get("total_count")) or not isinstance(data.get("check_runs"), list):
        return "GITHUB_BAD_RESPONSE:check_runs"
    runs = []
    for run in data["check_runs"]:
        if not isinstance(run, dict) or not isinstance(run.get("name"), str):
            return "GITHUB_BAD_RESPONSE:check_runs"
        runs.append([run["name"], _str(run.get("status")), _str(run.get("conclusion"))])
    runs.sort()
    ev["check_runs_total"] = data["total_count"]
    ev["check_runs"] = runs
    return ""


def _parse_status(ev: dict, data) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("state"), str) or not isinstance(data.get("statuses"), list):
        return "GITHUB_BAD_RESPONSE:status"
    if not _is_int(data.get("total_count")):
        return "GITHUB_BAD_RESPONSE:status"
    sts = []
    for s in data["statuses"]:
        if not isinstance(s, dict) or not isinstance(s.get("context"), str):
            return "GITHUB_BAD_RESPONSE:status"
        sts.append([s["context"], _str(s.get("state"))])
    sts.sort()
    ev["status_state"] = data["state"]
    ev["status_total"] = data["total_count"]
    ev["statuses"] = sts
    return ""


def _parse_pull(ev: dict, data) -> str:
    if not isinstance(data, dict) or not _is_int(data.get("number")):
        return "GITHUB_BAD_RESPONSE:pull"
    base = data.get("base")
    ev["pr_state"] = _str(data.get("state"))
    ev["pr_merged"] = data.get("merged") is True
    ev["pr_merged_at"] = _str(data.get("merged_at"))
    ev["pr_merged_ts"] = _iso_to_ts(data.get("merged_at"))
    ev["pr_base"] = _str(base.get("ref")) if isinstance(base, dict) else ""
    if data["number"] != ev["pr_number"]:
        return "GITHUB_BAD_RESPONSE:pull"
    return ""


def _parse_commits(ev: dict, data) -> str:
    if not isinstance(data, list):
        return "GITHUB_BAD_RESPONSE:commits"
    ev["commits_count"] = len(data)
    return ""


def _evaluate(ev: dict, c: dict) -> list:
    """Deterministic verdict from the evidence facts and the milestone criteria.

    Returns [verdict, reason, payout]. Any fetch error -> DEFER with that reason.
    Otherwise every enabled criterion is evaluated; the first FAIL wins, else
    the first DEFER, else PASS (payout = milestone amount).
    """
    if ev["fetch_error"] != "":
        return [DEFER, ev["fetch_error"], 0]
    results = []

    # (a) release
    if ev["release_tag"] != c["tag"]:
        results.append([FAIL, "TAG_MISMATCH"])
    elif ev["release_draft"]:
        results.append([FAIL, "RELEASE_IS_DRAFT"])
    elif ev["release_published_ts"] < 0:
        results.append([DEFER, "RELEASE_NOT_PUBLISHED"])
    elif ev["release_published_ts"] > c["deadline"]:
        results.append([FAIL, "RELEASE_AFTER_DEADLINE"])
    elif ev["release_published_ts"] < c["not_before"]:
        results.append([FAIL, "RELEASE_BEFORE_NOT_BEFORE"])
    else:
        results.append([OK, ""])

    # (b1) check runs
    if c["checks"] in [CHECKS_RUNS, CHECKS_BOTH]:
        cr = []
        runs = ev["check_runs"]
        if ev["check_runs_total"] != len(runs):
            cr.append([DEFER, "CHECKS_TRUNCATED"])
        else:
            req = c["required_checks"]
            considered = []
            if len(req) == 0:
                considered = runs
                if len(runs) == 0:
                    cr.append([FAIL, "CHECKS_NONE"])
            else:
                for name in req:
                    named = [r for r in runs if r[0] == name]
                    if len(named) == 0:
                        cr.append([FAIL, "REQUIRED_CHECK_MISSING:" + name])
                    considered = considered + named
            for r in considered:
                if r[1] != "completed":
                    cr.append([DEFER, "CHECK_PENDING:" + r[0]])
                elif r[2] not in PASSING_CONCLUSIONS:
                    cr.append([FAIL, "CHECK_FAILED:" + r[0]])
        if len(cr) == 0:
            cr.append([OK, ""])
        results = results + cr

    # (b2) combined status
    if c["checks"] in [CHECKS_STATUS, CHECKS_BOTH]:
        if ev["status_total"] == 0:
            results.append([FAIL, "STATUS_NONE"])
        elif ev["status_state"] == "success":
            results.append([OK, ""])
        elif ev["status_state"] == "pending":
            results.append([DEFER, "STATUS_PENDING"])
        else:
            results.append([FAIL, "STATUS_" + ev["status_state"].upper()])

    # (c) pull request
    if c["pr_number"] > 0:
        if not ev["pr_merged"] or ev["pr_merged_ts"] < 0:
            results.append([FAIL, "PR_NOT_MERGED"])
        elif ev["pr_base"] != c["pr_base"]:
            results.append([FAIL, "PR_WRONG_BASE"])
        elif ev["pr_merged_ts"] > c["deadline"]:
            results.append([FAIL, "PR_MERGED_AFTER_DEADLINE"])
        else:
            results.append([OK, ""])

    # (d) commit count
    if c["commit_author"] != "":
        if ev["commits_count"] < c["min_commits"]:
            results.append([FAIL, "TOO_FEW_COMMITS"])
        else:
            results.append([OK, ""])

    for r in results:
        if r[0] == FAIL:
            return [FAIL, r[1], 0]
    for r in results:
        if r[0] == DEFER:
            return [DEFER, r[1], 0]
    return [PASS, "", c["amount"]]


def _fetch_evidence(grant_id: str, index: int, c: dict) -> dict:
    """Non-deterministic: query GitHub and build the settlement evidence.
    Stops at the first failed request (saves rate limit; error -> DEFER)."""
    ev = _blank_evidence(grant_id, index, c)
    o = c["owner"]
    rp = c["repo"]

    def step(http_key: str, url: str, endpoint: str, parser) -> bool:
        st, data, err = _get_json(url, endpoint)
        ev[http_key] = st
        if err == "":
            err = parser(ev, data)
        if err != "":
            ev["fetch_error"] = err
            return False
        return True

    ok = step("release_http", _url_release(o, rp, c["tag"]), "release", _parse_release)
    ok = ok and step("commit_http", _url_commit(o, rp, c["tag"]), "commit", _parse_commit)
    if ok and c["checks"] in [CHECKS_RUNS, CHECKS_BOTH]:
        ok = step("check_runs_http", _url_check_runs(o, rp, ev["commit_sha"]), "check_runs", _parse_check_runs)
    if ok and c["checks"] in [CHECKS_STATUS, CHECKS_BOTH]:
        ok = step("status_http", _url_status(o, rp, ev["commit_sha"]), "status", _parse_status)
    if ok and c["pr_number"] > 0:
        ok = step("pr_http", _url_pull(o, rp, c["pr_number"]), "pull", _parse_pull)
    if ok and c["commit_author"] != "":
        ok = step(
            "commits_http",
            _url_commits(o, rp, c["commit_author"], c["commits_since"], c["commits_until"]),
            "commits",
            _parse_commits,
        )
    verdict = _evaluate(ev, c)
    ev["verdict"] = verdict[0]
    ev["reason"] = verdict[1]
    ev["payout"] = verdict[2]
    return ev


def _canonical(v) -> str:
    # json distinguishes true/1 and "1"/1, so this is a strict, typed equality
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def _evidence_matches(leader, mine: dict, c: dict) -> bool:
    """Validator equivalence: every key of the evidence must be identical to the
    validator's own independent fetch, and the leader's verdict must follow
    from the leader's own facts."""
    if not isinstance(leader, dict):
        return False
    if sorted(leader.keys()) != sorted(EVIDENCE_KEYS):
        return False
    if _canonical(leader) != _canonical(mine):
        return False
    recomputed = _evaluate(leader, c)
    return [leader["verdict"], leader["reason"], leader["payout"]] == recomputed
# ==== REUSED END ====


# --------------------------------------------------------------------------
# GrantFlow constants
# --------------------------------------------------------------------------

GF_SPEC_KEYS = SPEC_KEYS + ["label"]

MAX_TITLE = 80
MAX_DESCRIPTION = 600
MAX_LABEL = 80
MAX_PITCH = 400
MAX_APPLICATIONS = 50
MAX_PAGE = 50

G_OPEN = "OPEN"  # funded, no builder assigned, accepting applications
G_ACTIVE = "ACTIVE"  # builder assigned, at least one milestone not settled
G_CLOSED = "CLOSED"  # every milestone PAID / RECLAIMED / CANCELLED
G_CANCELLED = "CANCELLED"  # funder cancelled before assignment, remaining milestones refunded
GRANT_STATUSES = [G_OPEN, G_ACTIVE, G_CLOSED, G_CANCELLED]

A_PENDING = "PENDING"
A_ACCEPTED = "ACCEPTED"
A_NOT_SELECTED = "NOT_SELECTED"
A_WITHDRAWN = "WITHDRAWN"
A_ENDED = "ENDED"  # accepted builder later resigned

TERMINAL = [M_PAID, M_RECLAIMED, M_CANCELLED]

ZERO = Address(b"\x00" * 20)


def _check_text(v, what: str, min_len: int, max_len: int) -> str:
    if not isinstance(v, str) or len(v) < min_len or len(v) > max_len:
        raise gl.vm.UserError("Invalid " + what + " (" + str(min_len) + "-" + str(max_len) + " chars)")
    for ch in v:
        if ord(ch) < 32 and ch != "\n":
            raise gl.vm.UserError("Invalid " + what + " (control characters)")
    return v


# --------------------------------------------------------------------------
# Storage types
# --------------------------------------------------------------------------


@allow_storage
@dataclass
class Grant:
    id: str
    funder: Address
    builder: Address
    title: str
    description: str
    owner: str
    repo: str
    grace_seconds: u256
    created_at: u256
    assigned_at: u256
    milestone_count: u32
    application_count: u32
    deposited: u256
    paid: u256
    refunded: u256
    status: str


@allow_storage
@dataclass
class Milestone:
    label: str
    amount: u256
    deadline: u256
    not_before: u256
    tag: str
    checks: str
    required_checks_json: str
    pr_number: u256
    pr_base: str
    commit_author: str
    min_commits: u32
    commits_since: u256
    commits_until: u256
    status: str
    claims: u32
    verify_attempts: u32
    paid_amount: u256
    paid_to: Address
    last_verdict: str
    last_reason: str
    evidence: str


@allow_storage
@dataclass
class Application:
    grant_id: str
    index: u32
    builder: Address
    github_login: str
    pitch: str
    created_at: u256
    status: str


@gl.evm.contract_interface
class _Recipient:
    class View:
        pass

    class Write:
        pass


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


class GrantFlow(gl.Contract):
    grant_count: u256
    application_total: u256
    total_deposited: u256
    total_paid: u256
    total_refunded: u256
    verify_pass: u256
    verify_fail: u256
    verify_defer: u256
    grants: TreeMap[str, Grant]
    milestones: TreeMap[str, Milestone]
    applications: TreeMap[str, Application]
    open_application: TreeMap[str, str]  # "<grant_id>|<builder hex>" -> application key while PENDING
    funder_grants: TreeMap[str, DynArray[str]]
    builder_grants: TreeMap[str, DynArray[str]]
    builder_applications: TreeMap[str, DynArray[str]]

    def __init__(self):
        self.grant_count = u256(0)
        self.application_total = u256(0)
        self.total_deposited = u256(0)
        self.total_paid = u256(0)
        self.total_refunded = u256(0)
        self.verify_pass = u256(0)
        self.verify_fail = u256(0)
        self.verify_defer = u256(0)

    # ---------------- internal helpers ----------------

    def _now(self) -> int:
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - EPOCH) // datetime.timedelta(seconds=1)

    def _send(self, to: Address, amount: int) -> None:
        if amount > 0:
            _Recipient(to).emit_transfer(value=u256(amount))

    def _grant(self, grant_id: str) -> Grant:
        if not isinstance(grant_id, str) or grant_id not in self.grants:
            raise gl.vm.UserError("Unknown grant")
        return self.grants[grant_id]

    def _key(self, grant_id: str, index: int) -> str:
        g = self._grant(grant_id)
        if not _is_int(index) or index < 0 or index >= int(g.milestone_count):
            raise gl.vm.UserError("Unknown milestone")
        return grant_id + "/" + str(index)

    def _index_append(self, index: TreeMap[str, DynArray[str]], key: str, value: str) -> None:
        index.get_or_insert_default(key).append(value)

    def _index_list(self, index: TreeMap[str, DynArray[str]], key: str) -> list:
        if key not in index:
            return []
        return [str(x) for x in index[key]]

    def _addr(self, v, what: str) -> Address:
        try:
            a = Address(v)
        except Exception:
            raise gl.vm.UserError("Invalid " + what + " address")
        if a == ZERO:
            raise gl.vm.UserError("Invalid " + what + " address")
        return a

    def _criteria(self, g: Grant, m: Milestone) -> dict:
        return {
            "owner": str(g.owner),
            "repo": str(g.repo),
            "tag": str(m.tag),
            "deadline": int(m.deadline),
            "not_before": int(m.not_before),
            "checks": str(m.checks),
            "required_checks": json.loads(m.required_checks_json),
            "pr_number": int(m.pr_number),
            "pr_base": str(m.pr_base),
            "commit_author": str(m.commit_author),
            "min_commits": int(m.min_commits),
            "commits_since": int(m.commits_since),
            "commits_until": int(m.commits_until),
            "amount": int(m.amount),
        }

    def _validate_spec(self, spec, now: int) -> dict:
        if not isinstance(spec, dict):
            raise gl.vm.UserError("Milestone must be an object")
        for k in spec.keys():
            if k not in GF_SPEC_KEYS:
                raise gl.vm.UserError("Unknown milestone field: " + str(k))
        label = _check_text(spec.get("label", ""), "milestone label", 0, MAX_LABEL)
        amount = spec.get("amount")
        if not _is_int(amount) or amount <= 0:
            raise gl.vm.UserError("Invalid amount")
        deadline = spec.get("deadline")
        if not _is_int(deadline) or deadline <= now:
            raise gl.vm.UserError("Deadline must be in the future")
        if deadline > now + MAX_DEADLINE_AHEAD_S:
            raise gl.vm.UserError("Deadline too far ahead")
        not_before = spec.get("not_before", 0)
        if not _is_int(not_before) or not_before < 0 or not_before > deadline:
            raise gl.vm.UserError("Invalid not_before")
        tag = _check_name(spec.get("tag"), "tag", NAME_CHARS, 100)
        checks = spec.get("checks", CHECKS_NONE)
        if checks not in CHECK_MODES:
            raise gl.vm.UserError("Invalid checks mode")
        req = spec.get("required_checks", [])
        if not isinstance(req, list) or len(req) > MAX_REQUIRED_CHECKS:
            raise gl.vm.UserError("Invalid required_checks")
        for name in req:
            if not isinstance(name, str) or len(name) == 0 or len(name) > 100:
                raise gl.vm.UserError("Invalid required_checks")
        if len(req) != len(set(req)):
            raise gl.vm.UserError("Duplicate required check")
        if len(req) > 0 and checks not in [CHECKS_RUNS, CHECKS_BOTH]:
            raise gl.vm.UserError("required_checks need checks mode check_runs or both")
        pr_number = spec.get("pr_number", 0)
        if not _is_int(pr_number) or pr_number < 0:
            raise gl.vm.UserError("Invalid pr_number")
        pr_base = spec.get("pr_base", "")
        if pr_number > 0:
            pr_base = _check_name(pr_base, "pr_base", NAME_CHARS + "/", 100)
        elif pr_base != "":
            raise gl.vm.UserError("pr_base given without pr_number")
        author = spec.get("commit_author", "")
        min_commits = spec.get("min_commits", 0)
        since = spec.get("commits_since", 0)
        until = spec.get("commits_until", 0)
        if author != "":
            _check_name(author, "commit_author", LOGIN_CHARS, 39)
            if not _is_int(min_commits) or min_commits < 1 or min_commits > MAX_MIN_COMMITS:
                raise gl.vm.UserError("Invalid min_commits")
            if not _is_int(since) or not _is_int(until) or since < 0 or until <= since:
                raise gl.vm.UserError("Invalid commit range")
            if until > deadline:
                raise gl.vm.UserError("Commit range must end by the deadline")
        elif min_commits != 0 or since != 0 or until != 0:
            raise gl.vm.UserError("Commit criteria given without commit_author")
        return {
            "label": label,
            "amount": amount,
            "deadline": deadline,
            "not_before": not_before,
            "tag": tag,
            "checks": checks,
            "required_checks": req,
            "pr_number": pr_number,
            "pr_base": pr_base,
            "commit_author": author,
            "min_commits": min_commits,
            "commits_since": since,
            "commits_until": until,
        }

    def _refresh_status(self, g: Grant) -> None:
        if g.status == G_CANCELLED:
            return
        all_terminal = True
        for i in range(int(g.milestone_count)):
            if self.milestones[g.id + "/" + str(i)].status not in TERMINAL:
                all_terminal = False
        if all_terminal:
            g.status = G_CLOSED
        elif g.builder != ZERO:
            g.status = G_ACTIVE
        else:
            g.status = G_OPEN

    def _refund(self, g: Grant, m: Milestone, new_status: str) -> int:
        amount = int(m.amount)
        m.status = new_status
        g.refunded = u256(int(g.refunded) + amount)
        self.total_refunded = u256(int(self.total_refunded) + amount)
        self._send(g.funder, amount)
        return amount

    def _assign(self, g: Grant, builder: Address) -> None:
        g.builder = builder
        g.assigned_at = u256(self._now())
        self._index_append(self.builder_grants, builder.as_hex, g.id)
        self._refresh_status(g)

    # ---------------- funder ----------------

    @gl.public.write.payable
    def create_grant(
        self, title: str, description: str, owner: str, repo: str, grace_days: int, milestones: list, builder: str
    ) -> str:
        """Post a grant. msg.value must equal the sum of milestone amounts.
        builder = "" opens the grant for applications; an address assigns it directly."""
        funder = gl.message.sender_address
        _check_text(title, "title", 3, MAX_TITLE)
        _check_text(description, "description", 0, MAX_DESCRIPTION)
        _check_name(owner, "owner", NAME_CHARS, 100)
        _check_name(repo, "repo", NAME_CHARS, 100)
        if not _is_int(grace_days) or grace_days < MIN_GRACE_DAYS or grace_days > MAX_GRACE_DAYS:
            raise gl.vm.UserError("Invalid grace_days")
        if not isinstance(milestones, list) or len(milestones) == 0 or len(milestones) > MAX_MILESTONES:
            raise gl.vm.UserError("Need 1-20 milestones")
        builder_addr = ZERO
        if not isinstance(builder, str):
            raise gl.vm.UserError("Invalid builder address")
        if builder != "":
            builder_addr = self._addr(builder, "builder")
            if builder_addr == funder:
                raise gl.vm.UserError("Builder must differ from funder")
        now = self._now()
        specs = [self._validate_spec(s, now) for s in milestones]
        total = 0
        for s in specs:
            total += s["amount"]
        value = int(gl.message.value)
        if value < total:
            raise gl.vm.UserError("Insufficient deposit: send exactly " + str(total))
        if value > total:
            raise gl.vm.UserError("Deposit exceeds milestone total: send exactly " + str(total))

        new_count = int(self.grant_count) + 1
        grant_id = str(new_count)
        self.grant_count = u256(new_count)
        self.grants[grant_id] = Grant(
            id=grant_id,
            funder=funder,
            builder=ZERO,
            title=title,
            description=description,
            owner=owner,
            repo=repo,
            grace_seconds=u256(grace_days * DAY_S),
            created_at=u256(now),
            assigned_at=u256(0),
            milestone_count=u32(len(specs)),
            application_count=u32(0),
            deposited=u256(total),
            paid=u256(0),
            refunded=u256(0),
            status=G_OPEN,
        )
        for i in range(len(specs)):
            s = specs[i]
            self.milestones[grant_id + "/" + str(i)] = Milestone(
                label=s["label"],
                amount=u256(s["amount"]),
                deadline=u256(s["deadline"]),
                not_before=u256(s["not_before"]),
                tag=s["tag"],
                checks=s["checks"],
                required_checks_json=json.dumps(s["required_checks"]),
                pr_number=u256(s["pr_number"]),
                pr_base=s["pr_base"],
                commit_author=s["commit_author"],
                min_commits=u32(s["min_commits"]),
                commits_since=u256(s["commits_since"]),
                commits_until=u256(s["commits_until"]),
                status=M_OPEN,
                claims=u32(0),
                verify_attempts=u32(0),
                paid_amount=u256(0),
                paid_to=ZERO,
                last_verdict="",
                last_reason="",
                evidence="",
            )
        self.total_deposited = u256(int(self.total_deposited) + total)
        self._index_append(self.funder_grants, funder.as_hex, grant_id)
        if builder_addr != ZERO:
            self._assign(self.grants[grant_id], builder_addr)
        return grant_id

    @gl.public.write
    def assign_builder(self, grant_id: str, builder: str) -> None:
        """Funder picks one PENDING applicant. Other pending applications become NOT_SELECTED."""
        g = self._grant(grant_id)
        if gl.message.sender_address != g.funder:
            raise gl.vm.UserError("Only funder")
        if g.status != G_OPEN:
            raise gl.vm.UserError("Grant is not open for assignment (status " + g.status + ")")
        b = self._addr(builder, "builder")
        okey = grant_id + "|" + b.as_hex
        if okey not in self.open_application:
            raise gl.vm.UserError("Builder has no pending application")
        for i in range(int(g.application_count)):
            akey = grant_id + "/a" + str(i)
            a = self.applications[akey]
            if a.status == A_PENDING:
                a.status = A_ACCEPTED if a.builder == b else A_NOT_SELECTED
                del self.open_application[grant_id + "|" + a.builder.as_hex]
        self._assign(g, b)

    @gl.public.write
    def cancel_grant(self, grant_id: str) -> int:
        """Funder cancels a grant that has no builder assigned. Every milestone that is
        still OPEN is refunded to the funder. Not possible once a builder is assigned."""
        g = self._grant(grant_id)
        if gl.message.sender_address != g.funder:
            raise gl.vm.UserError("Only funder")
        if g.status != G_OPEN:
            raise gl.vm.UserError("Only grants without an assigned builder can be cancelled (status " + g.status + ")")
        refunded = 0
        for i in range(int(g.milestone_count)):
            m = self.milestones[grant_id + "/" + str(i)]
            if m.status == M_OPEN:
                refunded += self._refund(g, m, M_CANCELLED)
        for i in range(int(g.application_count)):
            a = self.applications[grant_id + "/a" + str(i)]
            if a.status == A_PENDING:
                a.status = A_NOT_SELECTED
                del self.open_application[grant_id + "|" + a.builder.as_hex]
        g.status = G_CANCELLED
        return refunded

    @gl.public.write
    def reclaim_milestone(self, grant_id: str, index: int) -> int:
        """Funder takes back a milestone that was not paid by deadline + grace."""
        key = self._key(grant_id, index)
        g = self.grants[grant_id]
        m = self.milestones[key]
        if gl.message.sender_address != g.funder:
            raise gl.vm.UserError("Only funder")
        if m.status not in [M_OPEN, M_CLAIMED]:
            raise gl.vm.UserError("Milestone not reclaimable in status " + m.status)
        if self._now() <= int(m.deadline) + int(g.grace_seconds):
            raise gl.vm.UserError("Deadline + grace not passed yet")
        amount = self._refund(g, m, M_RECLAIMED)
        self._refresh_status(g)
        return amount

    # ---------------- builder ----------------

    @gl.public.write
    def apply(self, grant_id: str, github_login: str, pitch: str) -> str:
        """Apply to an OPEN grant. github_login is self-reported and not verified."""
        g = self._grant(grant_id)
        sender = gl.message.sender_address
        if g.status != G_OPEN:
            raise gl.vm.UserError("Grant is not accepting applications (status " + g.status + ")")
        if sender == g.funder:
            raise gl.vm.UserError("Funder cannot apply to own grant")
        _check_name(github_login, "github_login", LOGIN_CHARS, 39)
        _check_text(pitch, "pitch", 1, MAX_PITCH)
        okey = grant_id + "|" + sender.as_hex
        if okey in self.open_application:
            raise gl.vm.UserError("You already have a pending application")
        n = int(g.application_count)
        if n >= MAX_APPLICATIONS:
            raise gl.vm.UserError("Application limit reached for this grant")
        akey = grant_id + "/a" + str(n)
        self.applications[akey] = Application(
            grant_id=grant_id,
            index=u32(n),
            builder=sender,
            github_login=github_login,
            pitch=pitch,
            created_at=u256(self._now()),
            status=A_PENDING,
        )
        g.application_count = u32(n + 1)
        self.open_application[okey] = akey
        self.application_total = u256(int(self.application_total) + 1)
        self._index_append(self.builder_applications, sender.as_hex, akey)
        return akey

    @gl.public.write
    def withdraw_application(self, grant_id: str) -> None:
        g = self._grant(grant_id)
        okey = grant_id + "|" + gl.message.sender_address.as_hex
        if okey not in self.open_application:
            raise gl.vm.UserError("No pending application")
        if g.status != G_OPEN:
            raise gl.vm.UserError("Grant is not open (status " + g.status + ")")
        self.applications[self.open_application[okey]].status = A_WITHDRAWN
        del self.open_application[okey]

    @gl.public.write
    def resign(self, grant_id: str) -> None:
        """Assigned builder steps down. Allowed only while no milestone is CLAIMED.
        Paid milestones stay paid; the grant re-opens for applications."""
        g = self._grant(grant_id)
        if g.status != G_ACTIVE or gl.message.sender_address != g.builder:
            raise gl.vm.UserError("Only the assigned builder of an active grant")
        for i in range(int(g.milestone_count)):
            if self.milestones[grant_id + "/" + str(i)].status == M_CLAIMED:
                raise gl.vm.UserError("A milestone is claimed; wait for its verification first")
        for i in range(int(g.application_count)):
            a = self.applications[grant_id + "/a" + str(i)]
            if a.status == A_ACCEPTED and a.builder == g.builder:
                a.status = A_ENDED
        g.builder = ZERO
        g.assigned_at = u256(0)
        self._refresh_status(g)

    @gl.public.write
    def claim_milestone(self, grant_id: str, index: int) -> None:
        key = self._key(grant_id, index)
        g = self.grants[grant_id]
        m = self.milestones[key]
        if g.builder == ZERO or gl.message.sender_address != g.builder:
            raise gl.vm.UserError("Only the assigned builder")
        if m.status != M_OPEN:
            raise gl.vm.UserError("Milestone not open (status " + m.status + ")")
        if self._now() > int(m.deadline):
            raise gl.vm.UserError("Deadline passed")
        m.status = M_CLAIMED
        m.claims = u32(int(m.claims) + 1)

    # ---------------- verification (anyone) ----------------

    @gl.public.write
    def verify_milestone(self, grant_id: str, index: int) -> dict:
        key = self._key(grant_id, index)
        g = self.grants[grant_id]
        m = self.milestones[key]
        if m.status != M_CLAIMED:
            raise gl.vm.UserError("Milestone not claimed (status " + m.status + ")")
        if g.builder == ZERO:
            raise gl.vm.UserError("No builder assigned")
        if self._now() > int(m.deadline) + int(g.grace_seconds):
            raise gl.vm.UserError("Verification window closed")

        # plain locals only inside the nondet block
        gid = str(grant_id)
        idx = int(index)
        crit = self._criteria(g, m)

        def leader_fn() -> dict:
            return _fetch_evidence(gid, idx, crit)

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            mine = _fetch_evidence(gid, idx, crit)
            return _evidence_matches(leaders_res.calldata, mine, crit)

        ev = gl.vm.run_nondet(leader_fn, validator_fn)

        # ---- deterministic post-consensus checks and integer money logic ----
        if not isinstance(ev, dict) or sorted(ev.keys()) != sorted(EVIDENCE_KEYS):
            raise gl.vm.UserError("Malformed evidence")
        expected = _blank_evidence(gid, idx, crit)
        for k in IDENTITY_KEYS:
            if _canonical(ev[k]) != _canonical(expected[k]):
                raise gl.vm.UserError("Evidence does not match milestone")
        verdict, reason, payout = _evaluate(ev, crit)
        if ev["verdict"] != verdict or ev["reason"] != reason or ev["payout"] != payout:
            raise gl.vm.UserError("Evidence verdict inconsistent")

        m.verify_attempts = u32(int(m.verify_attempts) + 1)
        m.last_verdict = verdict
        m.last_reason = reason
        m.evidence = _canonical(ev)
        if verdict == PASS:
            amount = int(m.amount)
            if payout != amount or int(m.paid_amount) != 0:
                raise gl.vm.UserError("Payout mismatch")
            m.status = M_PAID
            m.paid_amount = u256(amount)
            m.paid_to = g.builder
            g.paid = u256(int(g.paid) + amount)
            self.total_paid = u256(int(self.total_paid) + amount)
            self.verify_pass = u256(int(self.verify_pass) + 1)
            self._send(g.builder, amount)
        elif verdict == FAIL:
            m.status = M_OPEN  # builder may fix and re-claim before the deadline
            self.verify_fail = u256(int(self.verify_fail) + 1)
        else:
            self.verify_defer = u256(int(self.verify_defer) + 1)  # stays CLAIMED, retryable
        self._refresh_status(g)
        return ev

    # ---------------- views ----------------

    def _milestone_view(self, g: Grant, index: int) -> dict:
        m = self.milestones[g.id + "/" + str(index)]
        return {
            "index": index,
            "label": m.label,
            "amount": int(m.amount),
            "deadline": int(m.deadline),
            "deadline_iso": _ts_to_iso(int(m.deadline)),
            "reclaimable_after": int(m.deadline) + int(g.grace_seconds),
            "not_before": int(m.not_before),
            "tag": m.tag,
            "checks": m.checks,
            "required_checks": json.loads(m.required_checks_json),
            "pr_number": int(m.pr_number),
            "pr_base": m.pr_base,
            "commit_author": m.commit_author,
            "min_commits": int(m.min_commits),
            "commits_since": int(m.commits_since),
            "commits_until": int(m.commits_until),
            "status": m.status,
            "claims": int(m.claims),
            "verify_attempts": int(m.verify_attempts),
            "paid_amount": int(m.paid_amount),
            "paid_to": m.paid_to.as_hex if m.paid_to != ZERO else "",
            "last_verdict": m.last_verdict,
            "last_reason": m.last_reason,
            "evidence": m.evidence,
        }

    def _grant_view(self, g: Grant, with_milestones: bool) -> dict:
        locked = int(g.deposited) - int(g.paid) - int(g.refunded)
        out = {
            "id": g.id,
            "title": g.title,
            "description": g.description,
            "funder": g.funder.as_hex,
            "builder": g.builder.as_hex if g.builder != ZERO else "",
            "owner": g.owner,
            "repo": g.repo,
            "status": g.status,
            "grace_seconds": int(g.grace_seconds),
            "created_at": int(g.created_at),
            "assigned_at": int(g.assigned_at),
            "milestone_count": int(g.milestone_count),
            "application_count": int(g.application_count),
            "deposited": int(g.deposited),
            "paid": int(g.paid),
            "refunded": int(g.refunded),
            "locked": locked,
        }
        if with_milestones:
            out["milestones"] = [self._milestone_view(g, i) for i in range(int(g.milestone_count))]
        else:
            counts = {}
            for i in range(int(g.milestone_count)):
                st = self.milestones[g.id + "/" + str(i)].status
                counts[st] = counts.get(st, 0) + 1
            out["milestone_status_counts"] = counts
        return out

    def _application_view(self, a: Application) -> dict:
        return {
            "key": a.grant_id + "/a" + str(int(a.index)),
            "grant_id": a.grant_id,
            "index": int(a.index),
            "builder": a.builder.as_hex,
            "github_login": a.github_login,
            "pitch": a.pitch,
            "created_at": int(a.created_at),
            "status": a.status,
        }

    def _check_page(self, offset: int, limit: int) -> None:
        if not _is_int(offset) or not _is_int(limit) or offset < 0 or limit <= 0 or limit > MAX_PAGE:
            raise gl.vm.UserError("Invalid page (limit 1-50, offset >= 0)")

    @gl.public.view
    def get_stats(self) -> dict:
        by_status = {G_OPEN: 0, G_ACTIVE: 0, G_CLOSED: 0, G_CANCELLED: 0}
        for i in range(1, int(self.grant_count) + 1):
            by_status[self.grants[str(i)].status] += 1
        return {
            "grant_count": int(self.grant_count),
            "grants_by_status": by_status,
            "application_count": int(self.application_total),
            "total_deposited": int(self.total_deposited),
            "total_paid": int(self.total_paid),
            "total_refunded": int(self.total_refunded),
            "total_locked": int(self.total_deposited) - int(self.total_paid) - int(self.total_refunded),
            "verify_pass": int(self.verify_pass),
            "verify_fail": int(self.verify_fail),
            "verify_defer": int(self.verify_defer),
        }

    @gl.public.view
    def get_grant(self, grant_id: str) -> dict:
        return self._grant_view(self._grant(grant_id), True)

    @gl.public.view
    def get_milestone(self, grant_id: str, index: int) -> dict:
        self._key(grant_id, index)
        return self._milestone_view(self.grants[grant_id], index)

    @gl.public.view
    def list_grants(self, offset: int, limit: int) -> list:
        """Newest first. Summary views (no milestone details)."""
        self._check_page(offset, limit)
        out = []
        i = int(self.grant_count) - offset
        while i >= 1 and len(out) < limit:
            out.append(self._grant_view(self.grants[str(i)], False))
            i -= 1
        return out

    @gl.public.view
    def list_grants_by_status(self, status: str, offset: int, limit: int) -> list:
        """Newest first, filtered by grant status (OPEN, ACTIVE, CLOSED, CANCELLED)."""
        if status not in GRANT_STATUSES:
            raise gl.vm.UserError("Invalid status")
        self._check_page(offset, limit)
        out = []
        skipped = 0
        i = int(self.grant_count)
        while i >= 1 and len(out) < limit:
            g = self.grants[str(i)]
            if g.status == status:
                if skipped < offset:
                    skipped += 1
                else:
                    out.append(self._grant_view(g, False))
            i -= 1
        return out

    @gl.public.view
    def get_funder_grants(self, funder: str) -> list:
        key = self._addr(funder, "funder").as_hex
        return [self._grant_view(self.grants[i], False) for i in self._index_list(self.funder_grants, key)]

    @gl.public.view
    def get_builder_grants(self, builder: str) -> list:
        """Grants this address was assigned to (now or before resigning; see `builder`)."""
        key = self._addr(builder, "builder").as_hex
        return [self._grant_view(self.grants[i], False) for i in self._index_list(self.builder_grants, key)]

    @gl.public.view
    def get_grant_applications(self, grant_id: str) -> list:
        g = self._grant(grant_id)
        return [self._application_view(self.applications[grant_id + "/a" + str(i)]) for i in range(int(g.application_count))]

    @gl.public.view
    def get_builder_applications(self, builder: str) -> list:
        key = self._addr(builder, "builder").as_hex
        return [self._application_view(self.applications[k]) for k in self._index_list(self.builder_applications, key)]

    @gl.public.view
    def get_static_urls(self, grant_id: str, index: int) -> list:
        """GitHub API URLs that do not depend on the resolved commit SHA."""
        key = self._key(grant_id, index)
        g = self.grants[grant_id]
        m = self.milestones[key]
        urls = [_url_release(g.owner, g.repo, m.tag), _url_commit(g.owner, g.repo, m.tag)]
        if int(m.pr_number) > 0:
            urls.append(_url_pull(g.owner, g.repo, int(m.pr_number)))
        if m.commit_author != "":
            urls.append(_url_commits(g.owner, g.repo, m.commit_author, int(m.commits_since), int(m.commits_until)))
        return urls
