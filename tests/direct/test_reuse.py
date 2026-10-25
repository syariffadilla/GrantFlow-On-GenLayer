"""The GitHub evidence / verdict code is reused unchanged from MilestoneGrant.

The block between the REUSED markers in contracts/grantflow.py must be
byte-identical to lines 49-521 of contracts/milestone_grant.py in the accepted
MilestoneGrant contract (Studionet 0x8cD1213d653E5fADADa3B79feBb06fbbF4033211,
file sha256 4ba6e3d29721c2c0f78cc4032afc2b26ce8b2da12fe0f03cb5ed098a40586558).
tools/check_reuse.py compares the two files directly if you have both.
"""

import hashlib
from pathlib import Path

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "grantflow.py"

# sha256 of lines 49-521 of milestone_grant.py (constants + pure helpers), "\n"-joined, with a trailing "\n"
REUSED_SHA256 = "0484d7c6ed9ae374267da330624543d72fbb1cad5556a52e7a0d3ea6fd9a1f34"


def reused_block(text: str) -> str:
    i = text.index("# ==== REUSED BEGIN")
    i = text.index("\n", i) + 1
    j = text.index("# ==== REUSED END")
    return text[i:j]


def test_reused_block_is_unchanged():
    block = reused_block(CONTRACT.read_text())
    assert hashlib.sha256(block.encode()).hexdigest() == REUSED_SHA256


def test_reused_block_contains_the_verify_helpers():
    block = reused_block(CONTRACT.read_text())
    for name in ["def _fetch_evidence(", "def _evaluate(", "def _evidence_matches(", "def _blank_evidence(", "EVIDENCE_KEYS = [", "IDENTITY_KEYS = ["]:
        assert block.count(name) == 1, name


def test_new_code_does_not_redefine_reused_helpers():
    text = CONTRACT.read_text()
    after = text[text.index("# ==== REUSED END"):]
    for name in ["def _fetch_evidence(", "def _evaluate(", "def _evidence_matches(", "def _get_json(", "EVIDENCE_KEYS ="]:
        assert name not in after, name
