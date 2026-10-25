// Read-only: print the live state of the deployed contract (no key needed).
//   node scripts/read.mjs [0xContract]
import { contractAddress, json } from "./_env.mjs";
import { api, readClient } from "../src/lib/grantflow.js";

const A = contractAddress();
const c = readClient();
console.log("contract", A);
console.log("stats", json(await api.stats(c, A)));
for (const g of await api.grants(c, A, 0, 50)) {
  const full = await api.grant(c, A, g.id);
  full.milestones = full.milestones.map(({ evidence, ...m }) => ({ ...m, evidence_bytes: evidence.length }));
  console.log("grant", json(full));
  console.log("applications", json(await api.grantApplications(c, A, g.id)));
}
