"""Builder applications, assignment, withdrawal and resignation."""

import pytest

from tests.direct.conftest import (
    GEN,
    apply,
    assert_invariants,
    assign,
    claim,
    create,
    create_open,
    milestone,
    mock_github,
    to_hex,
    verify,
    warp,
)

AFTER_RELEASE = "2026-02-20T00:00:00Z"


@pytest.fixture
def open_grant(direct_vm, grant_contract, direct_owner):
    return create_open(direct_vm, grant_contract, direct_owner, [milestone(amount=GEN, label="m0"), milestone(amount=2 * GEN, label="m1")])


def test_apply_records_application(direct_vm, grant_contract, open_grant, direct_alice):
    key = apply(direct_vm, grant_contract, direct_alice, open_grant, login="alice-dev", pitch="Flask maintainer for 3 years.")
    assert key == open_grant + "/a0"
    apps = grant_contract.get_grant_applications(open_grant)
    assert len(apps) == 1
    a = apps[0]
    assert (a["builder"], a["github_login"], a["pitch"], a["status"]) == (to_hex(direct_alice), "alice-dev", "Flask maintainer for 3 years.", "PENDING")
    assert grant_contract.get_builder_applications(to_hex(direct_alice))[0]["key"] == key
    assert grant_contract.get_stats()["application_count"] == 1
    assert grant_contract.get_grant(open_grant)["application_count"] == 1


def test_apply_rules(direct_vm, grant_contract, open_grant, direct_owner, direct_alice):
    with direct_vm.expect_revert("Funder cannot apply to own grant"):
        apply(direct_vm, grant_contract, direct_owner, open_grant)
    with direct_vm.expect_revert("Invalid github_login"):
        apply(direct_vm, grant_contract, direct_alice, open_grant, login="bad login")
    with direct_vm.expect_revert("Invalid pitch"):
        apply(direct_vm, grant_contract, direct_alice, open_grant, pitch="")
    with direct_vm.expect_revert("Invalid pitch"):
        apply(direct_vm, grant_contract, direct_alice, open_grant, pitch="p" * 401)
    apply(direct_vm, grant_contract, direct_alice, open_grant)
    with direct_vm.expect_revert("already have a pending application"):
        apply(direct_vm, grant_contract, direct_alice, open_grant)
    with direct_vm.expect_revert("Unknown grant"):
        apply(direct_vm, grant_contract, direct_alice, "99")


def test_application_limit(direct_vm, grant_contract, open_grant):
    from eth_account import Account

    for i in range(50):
        direct_vm.sender = bytes.fromhex(Account.create().address[2:])
        grant_contract.apply(open_grant, "builder" + str(i), "pitch")
    direct_vm.sender = bytes.fromhex(Account.create().address[2:])
    with direct_vm.expect_revert("Application limit reached"):
        grant_contract.apply(open_grant, "late", "pitch")


def test_assign_picks_one_and_closes_the_rest(direct_vm, grant_contract, open_grant, direct_owner, direct_alice, direct_bob):
    apply(direct_vm, grant_contract, direct_alice, open_grant)
    apply(direct_vm, grant_contract, direct_bob, open_grant)
    assign(direct_vm, grant_contract, direct_owner, open_grant, direct_bob)
    g = grant_contract.get_grant(open_grant)
    assert g["status"] == "ACTIVE" and g["builder"] == to_hex(direct_bob)
    assert [a["status"] for a in grant_contract.get_grant_applications(open_grant)] == ["NOT_SELECTED", "ACCEPTED"]
    assert [x["id"] for x in grant_contract.get_builder_grants(to_hex(direct_bob))] == [open_grant]
    assert grant_contract.get_builder_grants(to_hex(direct_alice)) == []
    with direct_vm.expect_revert("not accepting applications (status ACTIVE)"):
        apply(direct_vm, grant_contract, direct_alice, open_grant)
    assert_invariants(grant_contract)


def test_assign_rules(direct_vm, grant_contract, open_grant, direct_owner, direct_alice, direct_bob):
    apply(direct_vm, grant_contract, direct_alice, open_grant)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only funder"):
        grant_contract.assign_builder(open_grant, to_hex(direct_alice))
    with direct_vm.expect_revert("Builder has no pending application"):
        assign(direct_vm, grant_contract, direct_owner, open_grant, direct_bob)
    assign(direct_vm, grant_contract, direct_owner, open_grant, direct_alice)
    with direct_vm.expect_revert("not open for assignment (status ACTIVE)"):
        assign(direct_vm, grant_contract, direct_owner, open_grant, direct_alice)


def test_only_assigned_builder_can_claim(direct_vm, grant_contract, open_grant, direct_owner, direct_alice, direct_bob):
    apply(direct_vm, grant_contract, direct_alice, open_grant)
    with direct_vm.expect_revert("Only the assigned builder"):
        claim(direct_vm, grant_contract, direct_alice, open_grant)  # applied, not assigned yet
    assign(direct_vm, grant_contract, direct_owner, open_grant, direct_alice)
    with direct_vm.expect_revert("Only the assigned builder"):
        claim(direct_vm, grant_contract, direct_bob, open_grant)
    claim(direct_vm, grant_contract, direct_alice, open_grant)
    assert grant_contract.get_milestone(open_grant, 0)["status"] == "CLAIMED"


def test_withdraw_application(direct_vm, grant_contract, open_grant, direct_owner, direct_alice):
    apply(direct_vm, grant_contract, direct_alice, open_grant)
    direct_vm.sender = direct_alice
    grant_contract.withdraw_application(open_grant)
    assert grant_contract.get_grant_applications(open_grant)[0]["status"] == "WITHDRAWN"
    with direct_vm.expect_revert("No pending application"):
        grant_contract.withdraw_application(open_grant)
    with direct_vm.expect_revert("Builder has no pending application"):
        assign(direct_vm, grant_contract, direct_owner, open_grant, direct_alice)
    apply(direct_vm, grant_contract, direct_alice, open_grant)  # may apply again
    assert [a["status"] for a in grant_contract.get_grant_applications(open_grant)] == ["WITHDRAWN", "PENDING"]


def test_resign_blocked_while_a_milestone_is_claimed(direct_vm, grant_contract, open_grant, direct_owner, direct_alice, direct_bob):
    apply(direct_vm, grant_contract, direct_alice, open_grant)
    assign(direct_vm, grant_contract, direct_owner, open_grant, direct_alice)
    claim(direct_vm, grant_contract, direct_alice, open_grant, 1)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("A milestone is claimed"):
        grant_contract.resign(open_grant)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the assigned builder of an active grant"):
        grant_contract.resign(open_grant)
    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("Only the assigned builder of an active grant"):
        grant_contract.resign(open_grant)
    assert grant_contract.get_grant(open_grant)["builder"] == to_hex(direct_alice)


def test_resign_then_reassign(direct_vm, grant_contract, open_grant, direct_owner, direct_alice, direct_bob, transfers):
    apply(direct_vm, grant_contract, direct_alice, open_grant)
    assign(direct_vm, grant_contract, direct_owner, open_grant, direct_alice)
    claim(direct_vm, grant_contract, direct_alice, open_grant, 0)
    warp(direct_vm, AFTER_RELEASE)
    mock_github(direct_vm)
    verify(direct_vm, grant_contract, direct_bob, open_grant, 0)  # PASS -> alice

    direct_vm.sender = direct_alice
    grant_contract.resign(open_grant)
    g = grant_contract.get_grant(open_grant)
    assert g["status"] == "OPEN" and g["builder"] == "" and g["paid"] == GEN
    assert grant_contract.get_grant_applications(open_grant)[0]["status"] == "ENDED"
    with direct_vm.expect_revert("Only the assigned builder"):
        claim(direct_vm, grant_contract, direct_alice, open_grant, 1)

    apply(direct_vm, grant_contract, direct_bob, open_grant)
    assign(direct_vm, grant_contract, direct_owner, open_grant, direct_bob)
    claim(direct_vm, grant_contract, direct_bob, open_grant, 1)
    verify(direct_vm, grant_contract, direct_owner, open_grant, 1)
    ms = grant_contract.get_grant(open_grant)["milestones"]
    assert ms[0]["paid_to"] == to_hex(direct_alice) and ms[1]["paid_to"] == to_hex(direct_bob)
    assert transfers == [(to_hex(direct_alice), GEN), (to_hex(direct_bob), 2 * GEN)]
    assert grant_contract.get_grant(open_grant)["status"] == "CLOSED"
    assert_invariants(grant_contract)


def test_direct_grant_has_no_application_needed(direct_vm, grant_contract, direct_owner, direct_alice):
    gid = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone()])
    claim(direct_vm, grant_contract, direct_alice, gid)
    with direct_vm.expect_revert("not accepting applications"):
        apply(direct_vm, grant_contract, direct_alice, gid)
