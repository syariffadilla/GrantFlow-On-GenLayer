// Deploy contracts/grantflow.py to GenLayer Studionet with genlayer-js.
//   node scripts/deploy.mjs [--fund-gen 100]
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { createClient } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";
import { REPO_ROOT, loadKey, json } from "./_env.mjs";

const code = fs.readFileSync(path.join(REPO_ROOT, "contracts", "grantflow.py"), "utf8");
const account = loadKey("funder");
const client = createClient({ chain: studionet, account });
console.log("deployer", account.address);
console.log("contract sha256", crypto.createHash("sha256").update(code).digest("hex"));

const fundIdx = process.argv.indexOf("--fund-gen");
if (fundIdx > 0) {
  const gen = Number(process.argv[fundIdx + 1]);
  const r = await client.request({ method: "sim_fundAccount", params: [account.address, gen * 1e18] });
  console.log("faucet", r, "balance", (await client.getBalance({ address: account.address })).toString());
}

const hash = await client.deployContract({ code, args: [] });
console.log("deploy tx", hash);
const receipt = await client.waitForTransactionReceipt({ hash, status: TransactionStatus.ACCEPTED, interval: 3000, retries: 100 });
const addr = receipt?.data?.contract_address || receipt?.txDataDecoded?.contractAddress || receipt?.recipient || receipt?.to_address;
console.log("status", receipt.statusName || receipt.status_name || receipt.status, "consensus", receipt.resultName || receipt.result_name);
console.log("contract", addr);
fs.writeFileSync(path.join(REPO_ROOT, "..", "grantflow-deploy-receipt.json"), json(receipt));
