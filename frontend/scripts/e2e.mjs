// Live end-to-end run against the deployed contract on GenLayer Studionet,
// using the same library as the web app (src/lib/grantflow.js).
// Each step is a separate invocation so long consensus rounds don't block:
//
//   node scripts/e2e.mjs faucet                # Studio faucet for builder + funder2 (test GEN)
//   node scripts/e2e.mjs grant1                # funder: OPEN grant on pallets/flask, 2 milestones (1.5 GEN)
//   node scripts/e2e.mjs apply <grantId>       # builder: apply
//   node scripts/e2e.mjs assign <grantId>      # funder: assign the builder
//   node scripts/e2e.mjs claim <grantId> <idx> # builder: claim a milestone
//   node scripts/e2e.mjs verify <grantId> <idx> [role]  # anyone: send verify_milestone (prints the hash)
//   node scripts/e2e.mjs wait <txhash>         # wait until ACCEPTED and print the outcome
//   node scripts/e2e.mjs finalized <txhash>    # print the current status of a tx (FINALIZED?)
//   node scripts/e2e.mjs grant-open            # funder: OPEN grant on pallets/click (left open for applications)
//   node scripts/e2e.mjs grant-cancel          # funder2: post an OPEN grant on psf/requests, then cancel it (refund)
//   node scripts/e2e.mjs balances              # GEN balances of the script accounts and the contract
//
// Results are appended to ../grantflow-e2e-log.json (outside the repo).
import fs from "node:fs";
import path from "node:path";
import { TransactionStatus } from "genlayer-js/types";
import { REPO_ROOT, loadKeyHex, contractAddress, json } from "./_env.mjs";
import { GEN, api, keyClient, readClient, studioFaucet, summarizeReceipt, weiToGen } from "../src/lib/grantflow.js";

const ADDRESS = contractAddress();
const LOG = path.join(REPO_ROOT, "..", "grantflow-e2e-log.json");
const accounts = {
  funder: keyClient(loadKeyHex("funder")),
  builder: keyClient(loadKeyHex("builder")),
  funder2: keyClient(loadKeyHex("funder2")),
};
const rc = readClient();
const DAY = 86400;
const iso = (s) => Math.floor(Date.parse(s) / 1000);

function log(step, data) {
  const all = fs.existsSync(LOG) ? JSON.parse(fs.readFileSync(LOG, "utf8")) : [];
  all.push({ at: new Date().toISOString(), contract: ADDRESS, step, data: JSON.parse(json(data)) });
  fs.writeFileSync(LOG, JSON.stringify(all, null, 1));
  console.log(step, json(data));
}

const cb = (stage, info) => console.log(" ", stage, info.hash ?? "", info.status ?? "");
const [step, a1, a2, a3] = process.argv.slice(2).filter((x) => !/^0x[0-9a-fA-F]{40}$/.test(x));

if (step === "faucet") {
  for (const role of ["builder", "funder2"]) {
    const r = await studioFaucet(rc, accounts[role].address, 20);
    log("faucet_" + role, { tx: r, address: accounts[role].address });
  }
} else if (step === "grant1") {
  const deadline = Math.floor(Date.now() / 1000) + 60 * DAY;
  const milestones = [
    {
      label: "Flask 3.1.3: release + required CI + PR #5924 + 10 commits",
      amount: GEN,
      deadline,
      tag: "3.1.3",
      checks: "both",
      required_checks: ["build", "typing", "3.12", "3.13", "Windows", "Mac"],
      pr_number: 5924,
      pr_base: "stable",
      commit_author: "davidism",
      min_commits: 10,
      commits_since: iso("2026-02-01T00:00:00Z"),
      commits_until: iso("2026-02-20T00:00:00Z"),
    },
    { label: "Flask 3.1.3: every check run green", amount: GEN / 2n, deadline, tag: "3.1.3", checks: "check_runs" },
  ];
  const r = await api.createGrant(
    accounts.funder.client,
    ADDRESS,
    {
      title: "Flask 3.1.3 release (demo grant)",
      description: "Demo grant on public data: pays for the pallets/flask 3.1.3 release milestones if GitHub shows them done.",
      owner: "pallets",
      repo: "flask",
      graceDays: 7,
      milestones,
      builder: "",
    },
    GEN + GEN / 2n,
    cb,
  );
  log("create_grant_flask", r);
} else if (step === "apply") {
  const r = await api.apply(accounts.builder.client, ADDRESS, a1, "e2e-builder", "Demo applicant from the GrantFlow e2e script (Studionet test account).", cb);
  log("apply", { grant: a1, builder: accounts.builder.address, ...r });
} else if (step === "assign") {
  const r = await api.assignBuilder(accounts.funder.client, ADDRESS, a1, accounts.builder.address, cb);
  log("assign_builder", { grant: a1, builder: accounts.builder.address, ...r });
} else if (step === "claim") {
  const r = await api.claim(accounts.builder.client, ADDRESS, a1, Number(a2), cb);
  log("claim_milestone", { grant: a1, index: Number(a2), ...r });
} else if (step === "verify") {
  const who = accounts[a3 || "funder2"];
  const hash = await who.client.writeContract({ address: ADDRESS, functionName: "verify_milestone", args: [String(a1), Number(a2)], value: 0n });
  log("verify_sent", { grant: a1, index: Number(a2), caller: who.address, hash });
} else if (step === "wait") {
  const receipt = await rc.waitForTransactionReceipt({ hash: a1, status: TransactionStatus.ACCEPTED, interval: 5000, retries: 22 });
  const s = summarizeReceipt(receipt);
  let ev = null;
  try {
    ev = typeof s.returned === "string" ? s.returned : s.returned;
  } catch {}
  log("tx_outcome", { ...s, returned: ev });
} else if (step === "finalized") {
  const tx = await rc.getTransaction({ hash: a1 });
  log("tx_status", { hash: a1, status: tx.statusName ?? tx.status_name ?? tx.status, result: tx.resultName ?? tx.result_name ?? null });
} else if (step === "grant-open") {
  const deadline = Math.floor(Date.now() / 1000) + 90 * DAY;
  const r = await api.createGrant(
    accounts.funder.client,
    ADDRESS,
    {
      title: "Click 8.5.0 release (demo, open for applications)",
      description: "Demo grant left OPEN so visitors can try applying. Pays if pallets/click release 8.5.0 is published by the deadline.",
      owner: "pallets",
      repo: "click",
      graceDays: 7,
      milestones: [{ label: "Release 8.5.0 published", amount: GEN / 5n, deadline, tag: "8.5.0", checks: "none" }],
      builder: "",
    },
    GEN / 5n,
    cb,
  );
  log("create_grant_click_open", r);
} else if (step === "grant-cancel") {
  const deadline = Math.floor(Date.now() / 1000) + 45 * DAY;
  const r = await api.createGrant(
    accounts.funder2.client,
    ADDRESS,
    {
      title: "Requests v2.34.2 CI (demo, cancelled)",
      description: "Demo of cancel_grant: posted by a second funder and cancelled before any builder was assigned.",
      owner: "psf",
      repo: "requests",
      graceDays: 3,
      milestones: [{ label: "v2.34.2 with all check runs green", amount: GEN / 4n, deadline, tag: "v2.34.2", checks: "check_runs" }],
      builder: "",
    },
    GEN / 4n,
    cb,
  );
  log("create_grant_requests", r);
  const gid = String(r.returned ?? "").replaceAll('"', "");
  const c = await api.cancelGrant(accounts.funder2.client, ADDRESS, gid, cb);
  log("cancel_grant", { grant: gid, ...c });
} else if (step === "balances") {
  const out = { contract: weiToGen(await rc.getBalance({ address: ADDRESS }), 6) };
  for (const [k, v] of Object.entries(accounts)) out[k] = { address: v.address, gen: weiToGen(await rc.getBalance({ address: v.address }), 6) };
  log("balances", out);
} else {
  console.log("unknown step; see header");
}
