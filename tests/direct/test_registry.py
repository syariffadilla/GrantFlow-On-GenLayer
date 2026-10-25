"""Grant registry: posting grants from many funders, input validation, read views."""

import pytest

from tests.direct.conftest import (
    GEN,
    assert_invariants,
    create,
    create_open,
    full_milestone,
    milestone,
    to_hex,
    ts,
)


# ------------------------------------------------------------------ posting


def test_open_grant_is_listed_and_indexed(direct_vm, grant_contract, direct_owner):
    gid = create_open(direct_vm, grant_contract, direct_owner, [milestone(amount=2 * GEN, label="v1 release"), milestone(amount=GEN)])
    assert gid == "1"
    g = grant_contract.get_grant(gid)
    assert g["status"] == "OPEN" and g["builder"] == "" and g["funder"] == to_hex(direct_owner)
    assert g["title"] == "Flask release grant" and (g["owner"], g["repo"]) == ("pallets", "flask")
    assert g["deposited"] == 3 * GEN and g["locked"] == 3 * GEN and g["milestone_count"] == 2
    assert [m["label"] for m in g["milestones"]] == ["v1 release", ""]
    assert [m["status"] for m in g["milestones"]] == ["OPEN", "OPEN"]
    s = assert_invariants(grant_contract)
    assert s["grants_by_status"] == {"OPEN": 1, "ACTIVE": 0, "CLOSED": 0, "CANCELLED": 0}
    assert [x["id"] for x in grant_contract.get_funder_grants(to_hex(direct_owner))] == ["1"]


def test_grant_with_named_builder_is_active(direct_vm, grant_contract, direct_owner, direct_alice):
    gid = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone()])
    g = grant_contract.get_grant(gid)
    assert g["status"] == "ACTIVE" and g["builder"] == to_hex(direct_alice) and g["assigned_at"] > 0
    assert [x["id"] for x in grant_contract.get_builder_grants(to_hex(direct_alice))] == [gid]
    assert grant_contract.get_grant_applications(gid) == []


def test_many_funders_many_grants(direct_vm, grant_contract, direct_owner, direct_alice, direct_bob, direct_charlie):
    g1 = create_open(direct_vm, grant_contract, direct_owner, [milestone(amount=GEN)])
    g2 = create(direct_vm, grant_contract, direct_alice, direct_charlie, [milestone(amount=2 * GEN)], repo="click")
    g3 = create_open(direct_vm, grant_contract, direct_bob, [milestone(amount=3 * GEN), milestone(amount=GEN)], owner="psf", repo="requests")
    assert (g1, g2, g3) == ("1", "2", "3")
    assert [x["id"] for x in grant_contract.get_funder_grants(to_hex(direct_alice))] == ["2"]
    assert [x["id"] for x in grant_contract.get_funder_grants(to_hex(direct_bob))] == ["3"]
    assert grant_contract.get_funder_grants(to_hex(direct_charlie)) == []
    assert [x["id"] for x in grant_contract.get_builder_grants(to_hex(direct_charlie))] == ["2"]
    s = assert_invariants(grant_contract)
    assert s["grant_count"] == 3 and s["total_deposited"] == 7 * GEN and s["total_locked"] == 7 * GEN
    assert s["grants_by_status"]["OPEN"] == 2 and s["grants_by_status"]["ACTIVE"] == 1


def test_full_criteria_schedule_is_stored(direct_vm, grant_contract, direct_owner, direct_alice):
    gid = create(direct_vm, grant_contract, direct_owner, direct_alice, [full_milestone(amount=GEN, label="3.1.3 with CI")])
    m = grant_contract.get_milestone(gid, 0)
    assert m["checks"] == "both" and m["required_checks"] == ["build", "typing", "3.12", "3.13", "Windows", "Mac"]
    assert m["pr_number"] == 5924 and m["pr_base"] == "stable" and m["commit_author"] == "davidism" and m["min_commits"] == 10
    assert m["reclaimable_after"] == m["deadline"] + 7 * 86400
    urls = grant_contract.get_static_urls(gid, 0)
    assert urls[0] == "https://api.github.com/repos/pallets/flask/releases/tags/3.1.3"
    assert urls[2] == "https://api.github.com/repos/pallets/flask/pulls/5924"
    assert len(urls) == 4


# ------------------------------------------------------------------ deposit


def test_exact_deposit_required(direct_vm, grant_contract, direct_owner, direct_alice):
    ms = [milestone(amount=GEN), milestone(amount=2 * GEN)]
    with direct_vm.expect_revert("Insufficient deposit: send exactly " + str(3 * GEN)):
        create(direct_vm, grant_contract, direct_owner, direct_alice, ms, value=3 * GEN - 1)
    with direct_vm.expect_revert("Deposit exceeds milestone total"):
        create(direct_vm, grant_contract, direct_owner, direct_alice, ms, value=3 * GEN + 1)
    assert grant_contract.get_stats()["grant_count"] == 0


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "kw, msg",
    [
        ({"title": "ab"}, "Invalid title"),
        ({"title": "x" * 81}, "Invalid title"),
        ({"title": "bad\x07title"}, "control characters"),
        ({"description": "d" * 601}, "Invalid description"),
        ({"owner": "pallets/evil"}, "Invalid owner"),
        ({"repo": ".."}, "Invalid repo"),
        ({"grace_days": 0}, "Invalid grace_days"),
        ({"grace_days": 91}, "Invalid grace_days"),
    ],
)
def test_grant_field_validation(direct_vm, grant_contract, direct_owner, direct_alice, kw, msg):
    with direct_vm.expect_revert(msg):
        create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone()], **kw)


@pytest.mark.parametrize(
    "override, msg",
    [
        ({"label": "l" * 81}, "Invalid milestone label"),
        ({"amount": 0}, "Invalid amount"),
        ({"deadline": ts("2025-12-31T00:00:00Z")}, "Deadline must be in the future"),
        ({"deadline": ts("2030-01-01T00:00:00Z")}, "Deadline too far ahead"),
        ({"tag": "v1 0"}, "Invalid tag"),
        ({"checks": "maybe"}, "Invalid checks mode"),
        ({"required_checks": ["build"]}, "required_checks need checks mode"),
        ({"pr_base": "main"}, "pr_base given without pr_number"),
        ({"min_commits": 3}, "Commit criteria given without commit_author"),
        ({"payout_to": "someone"}, "Unknown milestone field: payout_to"),
    ],
)
def test_milestone_validation(direct_vm, grant_contract, direct_owner, direct_alice, override, msg):
    m = milestone()
    m.update(override)
    with direct_vm.expect_revert(msg):
        create(direct_vm, grant_contract, direct_owner, direct_alice, [m])


def test_milestone_count_limits(direct_vm, grant_contract, direct_owner, direct_alice):
    with direct_vm.expect_revert("Need 1-20 milestones"):
        create(direct_vm, grant_contract, direct_owner, direct_alice, [])
    with direct_vm.expect_revert("Need 1-20 milestones"):
        create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone() for _ in range(21)])
    gid = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone() for _ in range(20)])
    assert grant_contract.get_grant(gid)["milestone_count"] == 20


def test_builder_address_rules(direct_vm, grant_contract, direct_owner):
    with direct_vm.expect_revert("Builder must differ from funder"):
        create(direct_vm, grant_contract, direct_owner, direct_owner, [milestone()])
    direct_vm.sender = direct_owner
    direct_vm.value = GEN
    with direct_vm.expect_revert("Invalid builder address"):
        grant_contract.create_grant("Title", "", "pallets", "flask", 7, [milestone()], "0x1234")
    with direct_vm.expect_revert("Invalid builder address"):
        grant_contract.create_grant("Title", "", "pallets", "flask", 7, [milestone()], "0x" + "00" * 20)
    direct_vm.value = 0


# ------------------------------------------------------------------ views


def _grants(direct_vm, c, funder, n):
    return [create_open(direct_vm, c, funder, [milestone(amount=GEN)], title=f"Grant number {i}") for i in range(n)]


def test_list_grants_newest_first_and_paging(direct_vm, grant_contract, direct_owner):
    _grants(direct_vm, grant_contract, direct_owner, 5)
    assert [g["id"] for g in grant_contract.list_grants(0, 50)] == ["5", "4", "3", "2", "1"]
    assert [g["id"] for g in grant_contract.list_grants(1, 2)] == ["4", "3"]
    assert [g["id"] for g in grant_contract.list_grants(4, 10)] == ["1"]
    assert grant_contract.list_grants(5, 10) == []
    row = grant_contract.list_grants(0, 1)[0]
    assert "milestones" not in row and row["milestone_status_counts"] == {"OPEN": 1}


@pytest.mark.parametrize("offset, limit", [(-1, 5), (0, 0), (0, 51)])
def test_list_grants_bad_page(direct_vm, grant_contract, offset, limit):
    with direct_vm.expect_revert("Invalid page"):
        grant_contract.list_grants(offset, limit)


def test_list_by_status(direct_vm, grant_contract, direct_owner, direct_alice):
    create_open(direct_vm, grant_contract, direct_owner, [milestone()])  # 1 OPEN
    create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone()])  # 2 ACTIVE
    create_open(direct_vm, grant_contract, direct_owner, [milestone()])  # 3 OPEN
    create_open(direct_vm, grant_contract, direct_owner, [milestone()])  # 4 -> CANCELLED
    direct_vm.sender = direct_owner
    grant_contract.cancel_grant("4")
    assert [g["id"] for g in grant_contract.list_grants_by_status("OPEN", 0, 50)] == ["3", "1"]
    assert [g["id"] for g in grant_contract.list_grants_by_status("OPEN", 1, 50)] == ["1"]
    assert [g["id"] for g in grant_contract.list_grants_by_status("ACTIVE", 0, 50)] == ["2"]
    assert [g["id"] for g in grant_contract.list_grants_by_status("CANCELLED", 0, 50)] == ["4"]
    assert grant_contract.list_grants_by_status("CLOSED", 0, 50) == []
    with direct_vm.expect_revert("Invalid status"):
        grant_contract.list_grants_by_status("PAID", 0, 10)
    assert_invariants(grant_contract)


def test_unknown_ids(direct_vm, grant_contract, direct_owner, direct_alice):
    with direct_vm.expect_revert("Unknown grant"):
        grant_contract.get_grant("1")
    gid = create(direct_vm, grant_contract, direct_owner, direct_alice, [milestone()])
    with direct_vm.expect_revert("Unknown milestone"):
        grant_contract.get_milestone(gid, 1)
    with direct_vm.expect_revert("Unknown milestone"):
        grant_contract.get_static_urls(gid, -1)
    with direct_vm.expect_revert("Unknown grant"):
        grant_contract.get_grant_applications("9")
