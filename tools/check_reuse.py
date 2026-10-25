"""Compare GrantFlow's reused block with the accepted MilestoneGrant contract.

    python tools/check_reuse.py path/to/genlayer-milestone-grant/contracts/milestone_grant.py

Prints MATCH if the block between the REUSED markers in contracts/grantflow.py
is byte-identical to lines 49-521 (constants + pure GitHub helpers) of
milestone_grant.py.
"""
import sys
from pathlib import Path

gf = (Path(__file__).resolve().parents[1] / "contracts" / "grantflow.py").read_text()
mg = Path(sys.argv[1]).read_text().split("\n")
i = gf.index("# ==== REUSED BEGIN")
i = gf.index("\n", i) + 1
j = gf.index("# ==== REUSED END")
ours = gf[i:j]
theirs = "\n".join(mg[48:521]).rstrip("\n") + "\n"
print("MATCH" if ours == theirs else "DIFFERENT")
sys.exit(0 if ours == theirs else 1)
