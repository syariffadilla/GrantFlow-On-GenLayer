"""Grant lifecycle: cancel before assignment, reclaim after deadline + grace,
multi-milestone schedules, isolation between grants of different funders, stats.

GitHub data: the real pallets/flask 3.1.3 captures (see fixtures/README.md).
"""

from tests.direct.conftest import (
    FAILING_CHECK,
    GEN,
    apply,
    assert_invariants,
    assign,
    claim,
    create,
    create_open,
    full_milestone,
    milestone,
    mock_github,
    to_hex,
    ts,
    verify,
    warp,
)

AFTER_RELEASE = "2026-02-20T00:00:00Z"
DEADLINE = ts("2026-03-01T00:00:00Z")  # default milestone deadline in conftest
GRACE = 7 * 86400


# ------------------------------------------------------------------ cancel_grant


def test_cancel_open_grant_refunds_everything(direct_vm, grant_contract, direct_owner, direct_alice, transfers):
    gid = create_open(direct_vm, grant_contract, direct_owner, [milestone(amount=GEN), milestone(amount=2 * GEN)])
    apply(direct_vm, grant_contract, direct_alice, gid)
    direct_vm.sender = direct_owner
    assert grant_contract.cancel_grant(gid) == 3 * GEN
    g = grant_contract.get_grant(gid)
    assert g["status"] == "CANCELLED" and g["refunded"] == 3 * GEN and g["locked"] == 0
    assert [m["status"] for m in g["milestones"]] == ["CANCELLED", "CANCELLED"]
    assert grant_contract.get_grant_applications(gid)[0]["status"] == "NOT_SELECTED"
    assert transfers == [(to_hex(direct_owner), GEN), (to_hex(direct_owner), 2 * GEN)]
    with direct_vm.expect_revert("Only grants without an assigned builder"):
        grant_contract.cancel_grant(gid)
    with direct_vm.expect_revert("not accepting applications (status CANCELLED)"):
        apply(direct_vm, grant_contract, direct_alice, gid)
    assert_invariants(grant_contract)


def test_cancel_rules(direct_vm, grant_contract, direct_owner, direct_alice, direct_bob):
    gid = create_open(direct_vm, grant_contract, direct_owner, [milestone()])
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only funder"):
        grant_contract.cancel_grant(gid)
    apply(direct_vm, grant_contract, direct_alice, gid)
    assign(direct_vm, grant_contract, direct_owner, gid, direct_alice)
    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("Only grants without an assigned builder can be cancelled (status ACTIVE)"):
        grant_contract.cancel_grant(gid)


def test_cancel_after_resign_keeps_paid_milestone(direct_vm, grant_contract, direct_owner, direct_alice, transfers):
    gid = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone(amount=GEN), milestone(amount=4 * GEN)])
    claim(direct_vm, grant_contract, direct_alice, gid, 0)
    warp(direct_vm, AFTER_RELEASE)
    mock_github(direct_vm)
    verify(direct_vm, grant_contract, direct_alice, gid, 0)
    direct_vm.sender = direct_alice
    grant_contract.resign(gid)
    direct_vm.sender = direct_owner
    assert grant_contract.cancel_grant(gid) == 4 * GEN
    g = grant_contract.get_grant(gid)
    assert [m["status"] for m in g["milestones"]] == ["PAID", "CANCELLED"]
    assert g["paid"] == GEN and g["refunded"] == 4 * GEN and g["status"] == "CANCELLED"
    assert transfers == [(to_hex(direct_alice), GEN), (to_hex(direct_owner), 4 * GEN)]
    assert_invariants(grant_contract)


# ------------------------------------------------------------------ reclaim


def test_reclaim_only_after_deadline_plus_grace(direct_vm, grant_contract, direct_owner, direct_alice, transfers):
    gid = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone(amount=GEN), milestone(amount=2 * GEN)])
    claim(direct_vm, grant_contract, direct_alice, gid, 1)  # claimed but never verified
    direct_vm.sender = direct_owner
    warp(direct_vm, "2026-03-08T00:00:00Z")  # exactly deadline + grace
    with direct_vm.expect_revert("Deadline + grace not passed yet"):
        grant_contract.reclaim_milestone(gid, 0)
    warp(direct_vm, "2026-03-08T00:00:01Z")
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Only funder"):
        grant_contract.reclaim_milestone(gid, 0)
    direct_vm.sender = direct_owner
    assert grant_contract.reclaim_milestone(gid, 0) == GEN
    assert grant_contract.get_grant(gid)["status"] == "ACTIVE"
    assert grant_contract.reclaim_milestone(gid, 1) == 2 * GEN  # CLAIMED is reclaimable too
    g = grant_contract.get_grant(gid)
    assert g["status"] == "CLOSED" and g["refunded"] == 3 * GEN
    assert transfers == [(to_hex(direct_owner), GEN), (to_hex(direct_owner), 2 * GEN)]
    with direct_vm.expect_revert("not reclaimable in status RECLAIMED"):
        grant_contract.reclaim_milestone(gid, 0)
    assert_invariants(grant_contract)


def test_verify_window_closes_with_grace(direct_vm, grant_contract, direct_owner, direct_alice):
    gid = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone()])
    claim(direct_vm, grant_contract, direct_alice, gid)
    warp(direct_vm, "2026-03-08T00:00:01Z")
    mock_github(direct_vm)
    with direct_vm.expect_revert("Verification window closed"):
        verify(direct_vm, grant_contract, direct_alice, gid)


def test_claim_after_deadline_refused(direct_vm, grant_contract, direct_owner, direct_alice):
    gid = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone()])
    warp(direct_vm, "2026-03-01T00:00:01Z")
    with direct_vm.expect_revert("Deadline passed"):
        claim(direct_vm, grant_contract, direct_alice, gid)


# ------------------------------------------------------------------ schedules


def test_three_milestone_schedule_pass_fail_reclaim(direct_vm, grant_contract, direct_owner, direct_alice, direct_bob, transfers):
    """m0 full criteria -> PASS; m1 all check runs required -> FAIL (real 'Development Versions'
    failure), re-opened, never fixed -> reclaimed; m2 release-only -> PASS."""
    ms = [
        full_milestone(amount=GEN, label="release + CI + PR + commits"),
        milestone(amount=2 * GEN, checks="check_runs", label="all CI green"),
        milestone(amount=3 * GEN, label="release published"),
    ]
    gid = create_open(direct_vm, grant_contract, direct_owner, ms)
    apply(direct_vm, grant_contract, direct_alice, gid)
    assign(direct_vm, grant_contract, direct_owner, gid, direct_alice)
    for i in range(3):
        claim(direct_vm, grant_contract, direct_alice, gid, i)
    warp(direct_vm, AFTER_RELEASE)
    mock_github(direct_vm)
    assert verify(direct_vm, grant_contract, direct_bob, gid, 0)["verdict"] == "PASS"
    ev = verify(direct_vm, grant_contract, direct_bob, gid, 1)
    assert (ev["verdict"], ev["reason"], ev["payout"]) == ("FAIL", "CHECK_FAILED:" + FAILING_CHECK, 0)
    assert verify(direct_vm, grant_contract, direct_bob, gid, 2)["verdict"] == "PASS"
    g = grant_contract.get_grant(gid)
    assert [m["status"] for m in g["milestones"]] == ["PAID", "OPEN", "PAID"]
    assert g["status"] == "ACTIVE" and g["paid"] == 4 * GEN and g["locked"] == 2 * GEN
    warp(direct_vm, "2026-03-09T00:00:00Z")
    direct_vm.sender = direct_owner
    grant_contract.reclaim_milestone(gid, 1)
    g = grant_contract.get_grant(gid)
    assert g["status"] == "CLOSED" and g["locked"] == 0
    assert transfers == [(to_hex(direct_alice), GEN), (to_hex(direct_alice), 3 * GEN), (to_hex(direct_owner), 2 * GEN)]
    s = assert_invariants(grant_contract)
    assert (s["verify_pass"], s["verify_fail"], s["verify_defer"]) == (2, 1, 0)
    assert s["grants_by_status"]["CLOSED"] == 1


def test_defer_counts_and_keeps_claim(direct_vm, grant_contract, direct_owner, direct_alice):
    gid = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone(amount=GEN)])
    claim(direct_vm, grant_contract, direct_alice, gid)
    warp(direct_vm, AFTER_RELEASE)
    mock_github(direct_vm, release="{}", release_status=503)
    ev = verify(direct_vm, grant_contract, direct_alice, gid)
    assert ev["verdict"] == "DEFER" and ev["reason"] == "GITHUB_HTTP_503:release"
    assert grant_contract.get_milestone(gid, 0)["status"] == "CLAIMED"
    assert grant_contract.get_stats()["verify_defer"] == 1


# ------------------------------------------------------------------ isolation


def test_grants_of_different_funders_are_isolated(direct_vm, grant_contract, direct_owner, direct_alice, direct_bob, direct_charlie, transfers):
    g1 = create(direct_vm, grant_contract, direct_owner, direct_charlie, [milestone(amount=GEN)])
    g2 = create(direct_vm, grant_contract, direct_alice, direct_charlie, [milestone(amount=5 * GEN)])
    g3 = create(direct_vm, grant_contract, direct_bob, direct_alice, [milestone(amount=7 * GEN)])
    claim(direct_vm, grant_contract, direct_charlie, g2)
    warp(direct_vm, AFTER_RELEASE)
    mock_github(direct_vm)
    verify(direct_vm, grant_contract, direct_owner, g2)
    assert transfers == [(to_hex(direct_charlie), 5 * GEN)]
    assert grant_contract.get_grant(g1)["locked"] == GEN and grant_contract.get_grant(g1)["milestones"][0]["status"] == "OPEN"
    assert grant_contract.get_grant(g3)["locked"] == 7 * GEN
    # funder of one grant cannot touch another funder's grant
    warp(direct_vm, "2026-03-09T00:00:00Z")
    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("Only funder"):
        grant_contract.reclaim_milestone(g3, 0)
    direct_vm.sender = direct_bob
    assert grant_contract.reclaim_milestone(g3, 0) == 7 * GEN
    assert transfers[-1] == (to_hex(direct_bob), 7 * GEN)
    s = assert_invariants(grant_contract)
    assert s["total_deposited"] == 13 * GEN and s["total_paid"] == 5 * GEN and s["total_refunded"] == 7 * GEN
    assert s["total_locked"] == GEN


def test_evidence_binds_grant_id(direct_vm, grant_contract, direct_owner, direct_alice):
    """Two grants with identical criteria: each stored evidence names its own grant id."""
    g1 = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone()])
    g2 = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone()])
    claim(direct_vm, grant_contract, direct_alice, g1)
    claim(direct_vm, grant_contract, direct_alice, g2)
    warp(direct_vm, AFTER_RELEASE)
    mock_github(direct_vm)
    e1 = verify(direct_vm, grant_contract, direct_alice, g1)
    e2 = verify(direct_vm, grant_contract, direct_alice, g2)
    assert (e1["grant_id"], e2["grant_id"]) == (g1, g2)
