"""A second funder's grant on a second repository (psf/requests, release v2.34.2),
verified on real GitHub captures taken on 2026-10-25 (see fixtures/README.md).

Real facts: release v2.34.2 published 2026-05-14T19:27:15Z, tag commit 6e83187b...,
66 latest check runs, all completed with success or skipped; combined commit
status has 0 contexts (state "pending", total_count 0).
"""

import json

from tests.direct.conftest import (
    GEN,
    API,
    assert_invariants,
    claim,
    create,
    fixture_json,
    fixture_text,
    mock,
    to_hex,
    ts,
    verify,
    warp,
)

import re

SHA = "6e83187b8feb273ed4c6cdab5efd8d54901dfab3"
TAG = "v2.34.2"
BASE = API + "psf/requests"
NOW = "2026-06-01T00:00:00Z"
DEADLINE = ts("2026-07-01T00:00:00Z")


def _mock(vm):
    mock(vm, BASE + "/releases/tags/" + re.escape(TAG) + "$", fixture_text("release_requests_v2.34.2.json"))
    mock(vm, BASE + "/commits/refs/tags/" + re.escape(TAG) + "$", fixture_text("commit_requests_v2.34.2.json"))
    mock(vm, BASE + "/commits/" + SHA + r"/check-runs\?filter=latest&per_page=100$", fixture_text("check_runs_requests_v2.34.2.json"))
    mock(vm, BASE + "/commits/" + SHA + r"/status\?per_page=100$", fixture_text("status_requests_v2.34.2.json"))


def _grant(vm, c, funder, builder, checks, amount=2 * GEN):
    warp(vm, NOW)
    return create(
        vm, c, funder, builder,
        [{"amount": amount, "deadline": DEADLINE, "tag": TAG, "checks": checks, "label": "requests " + TAG}],
        owner="psf", repo="requests", title="Requests release grant",
    )


def test_fixture_facts():
    rel = fixture_json("release_requests_v2.34.2.json")
    assert rel["tag_name"] == TAG and rel["draft"] is False
    runs = fixture_json("check_runs_requests_v2.34.2.json")
    assert runs["total_count"] == len(runs["check_runs"]) == 66
    assert all(r["status"] == "completed" and r["conclusion"] in ("success", "skipped") for r in runs["check_runs"])
    st = fixture_json("status_requests_v2.34.2.json")
    assert st["state"] == "pending" and st["total_count"] == 0


def test_requests_check_runs_pass(direct_vm, grant_contract, direct_bob, direct_charlie, transfers):
    gid = _grant(direct_vm, grant_contract, direct_bob, direct_charlie, "check_runs")
    claim(direct_vm, grant_contract, direct_charlie, gid)
    _mock(direct_vm)
    ev = verify(direct_vm, grant_contract, direct_bob, gid)
    assert (ev["verdict"], ev["reason"], ev["payout"]) == ("PASS", "", 2 * GEN)
    assert (ev["owner"], ev["repo"], ev["commit_sha"]) == ("psf", "requests", SHA)
    assert ev["check_runs_total"] == 66 and len(ev["check_runs"]) == 66
    assert transfers == [(to_hex(direct_charlie), 2 * GEN)]
    assert grant_contract.get_grant(gid)["status"] == "CLOSED"
    assert_invariants(grant_contract)


def test_requests_both_fails_without_commit_status(direct_vm, grant_contract, direct_bob, direct_charlie, transfers):
    gid = _grant(direct_vm, grant_contract, direct_bob, direct_charlie, "both")
    claim(direct_vm, grant_contract, direct_charlie, gid)
    _mock(direct_vm)
    ev = verify(direct_vm, grant_contract, direct_bob, gid)
    assert (ev["verdict"], ev["reason"], ev["payout"]) == ("FAIL", "STATUS_NONE", 0)
    assert ev["status_http"] == 200 and ev["status_total"] == 0
    assert transfers == []
    m = grant_contract.get_milestone(gid, 0)
    assert m["status"] == "OPEN" and json.loads(m["evidence"])["reason"] == "STATUS_NONE"


def test_validator_rejects_leader_from_other_repo_data(direct_vm, grant_contract, direct_bob, direct_charlie):
    """A leader that reports flask evidence for a requests milestone is rejected."""
    gid = _grant(direct_vm, grant_contract, direct_bob, direct_charlie, "check_runs")
    claim(direct_vm, grant_contract, direct_charlie, gid)
    _mock(direct_vm)
    ev = verify(direct_vm, grant_contract, direct_bob, gid)
    assert direct_vm.run_validator() is True
    forged = dict(ev)
    forged["repo"] = "flask"
    assert direct_vm.run_validator(leader_result=forged) is False
    forged = dict(ev)
    forged["check_runs"] = ev["check_runs"][:-1]
    assert direct_vm.run_validator(leader_result=forged) is False
