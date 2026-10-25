// GrantFlow client library: thin wrapper around genlayer-js used by BOTH the
// browser app (src/main.js) and the Node scripts (scripts/*.mjs), so the
// live end-to-end script exercises the same code paths as the UI.
//
// Wallet note: this does NOT call genlayer-js client.connect(), which requires the
// GenLayer MetaMask Snap. ensureStudionet() adds/switches the network with the
// standard wallet_addEthereumChain / wallet_switchEthereumChain requests instead.
import { createClient, createAccount, generatePrivateKey } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";

export const GEN = 10n ** 18n;
export const CHAIN = studionet;

// ------------------------------------------------------------------ units

/** "1.5" GEN -> wei (bigint), no floats. */
export function genToWei(s) {
  const str = String(s).trim();
  if (!/^\d+(\.\d{0,18})?$/.test(str)) throw new Error(`Invalid GEN amount: ${s}`);
  const [whole, frac = ""] = str.split(".");
  return BigInt(whole) * GEN + BigInt((frac + "0".repeat(18)).slice(0, 18));
}

/** wei -> "1.5" (trimmed). */
export function weiToGen(w, maxDecimals = 4) {
  const v = BigInt(w);
  const neg = v < 0n;
  const a = neg ? -v : v;
  const whole = a / GEN;
  let frac = (a % GEN).toString().padStart(18, "0").slice(0, maxDecimals).replace(/0+$/, "");
  return (neg ? "-" : "") + whole.toString() + (frac ? "." + frac : "");
}

// ------------------------------------------------------------------ clients

export function readClient() {
  return createClient({ chain: CHAIN });
}

/** Client signing with a local private key (burner / script account). */
export function keyClient(privateKey) {
  const account = createAccount(privateKey);
  return { client: createClient({ chain: CHAIN, account }), address: account.address, account };
}

export function newBurnerKey() {
  return generatePrivateKey();
}

/** Client signing through an injected wallet (MetaMask). */
export async function walletClient(provider) {
  const [address] = await provider.request({ method: "eth_requestAccounts" });
  await ensureStudionet(provider);
  const client = createClient({ chain: CHAIN, account: address, provider });
  return { client, address };
}

/**
 * Add/switch the wallet to GenLayer Studionet. Unlike genlayer-js connect(), this does
 * not require the GenLayer MetaMask Snap, which many wallets (or users) reject.
 */
async function ensureStudionet(provider) {
  const chainId = "0x" + CHAIN.id.toString(16);
  const current = await provider.request({ method: "eth_chainId" });
  if (current === chainId) return;
  try {
    await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId }] });
  } catch (e) {
    if (e?.code !== 4902 && !/unrecognized|not added|unknown chain/i.test(e?.message || "")) throw e;
    await provider.request({
      method: "wallet_addEthereumChain",
      params: [{
        chainId,
        chainName: CHAIN.name,
        rpcUrls: CHAIN.rpcUrls.default.http,
        nativeCurrency: CHAIN.nativeCurrency,
        blockExplorerUrls: CHAIN.blockExplorers?.default?.url ? [CHAIN.blockExplorers.default.url] : undefined,
      }],
    });
  }
}

/** Studio-only faucet (simulated balances). */
export async function studioFaucet(client, address, gen) {
  return client.request({ method: "sim_fundAccount", params: [address, Number(gen) * 1e18] });
}

export async function balanceOf(client, address) {
  return client.getBalance({ address });
}

// ------------------------------------------------------------------ calls

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** View call with a few retries: the public Studio RPC occasionally drops a request
 * (seen in the browser as a CORS/"Failed to fetch" error) right after a write. */
export async function read(client, address, functionName, args = [], attempts = 4) {
  let last;
  for (let i = 0; i < attempts; i++) {
    try {
      return await client.readContract({ address, functionName, args });
    } catch (e) {
      last = e;
      const msg = String(e?.details || e?.message || "");
      if (/execution failed/i.test(msg)) throw e; // contract-level error (e.g. unknown id): don't retry
      await sleep(1500 * (i + 1));
    }
  }
  throw last;
}

/** Pull the useful bits out of a GenLayer receipt. */
export function summarizeReceipt(receipt) {
  const lr0 = receipt?.consensus_data?.leader_receipt;
  const lr = Array.isArray(lr0) ? lr0[0] : lr0;
  let error = null;
  let returned = null;
  const res = lr?.result;
  if (res && typeof res === "object") {
    if (res.status === "return") returned = res.payload?.readable ?? null;
    else error = res.payload?.readable ?? res.payload ?? res.status;
  } else if (typeof res === "string") {
    returned = res;
  }
  if (!error && lr?.execution_result && lr.execution_result !== "SUCCESS") {
    error = lr?.genvm_result?.stderr || lr.execution_result;
  }
  return {
    hash: receipt?.hash,
    status: receipt?.status_name ?? receipt?.statusName ?? String(receipt?.status),
    consensus: receipt?.result_name ?? receipt?.resultName ?? null,
    execution: lr?.execution_result ?? null,
    ok: (lr?.execution_result ?? "SUCCESS") === "SUCCESS" && !error,
    returned,
    error,
    votes: receipt?.consensus_data?.votes ?? null,
  };
}

/**
 * Send a write transaction and wait until it is ACCEPTED.
 * onUpdate(stage, info) is called with "sent" (hash) and "accepted" (summary).
 */
export async function write(client, address, functionName, args = [], value = 0n, onUpdate = () => {}, opts = {}) {
  const hash = await client.writeContract({ address, functionName, args, value: BigInt(value) });
  onUpdate("sent", { hash });
  const receipt = await client.waitForTransactionReceipt({
    hash,
    status: TransactionStatus.ACCEPTED,
    interval: opts.interval ?? 4000,
    retries: opts.retries ?? 150,
  });
  const summary = summarizeReceipt(receipt);
  summary.hash = summary.hash || hash;
  onUpdate("accepted", summary);
  return summary;
}

// ------------------------------------------------------------------ contract API

export const api = {
  stats: (c, a) => read(c, a, "get_stats"),
  grants: (c, a, offset = 0, limit = 50) => read(c, a, "list_grants", [offset, limit]),
  grantsByStatus: (c, a, status, offset = 0, limit = 50) => read(c, a, "list_grants_by_status", [status, offset, limit]),
  grant: (c, a, id) => read(c, a, "get_grant", [String(id)]),
  milestone: (c, a, id, idx) => read(c, a, "get_milestone", [String(id), Number(idx)]),
  funderGrants: (c, a, who) => read(c, a, "get_funder_grants", [who]),
  builderGrants: (c, a, who) => read(c, a, "get_builder_grants", [who]),
  grantApplications: (c, a, id) => read(c, a, "get_grant_applications", [String(id)]),
  builderApplications: (c, a, who) => read(c, a, "get_builder_applications", [who]),
  staticUrls: (c, a, id, idx) => read(c, a, "get_static_urls", [String(id), Number(idx)]),

  /** g = {title, description, owner, repo, graceDays, milestones: [spec...], builder: "" | 0x...}; value = exact sum (wei). */
  createGrant: (c, a, g, value, cb) =>
    write(c, a, "create_grant", [g.title, g.description, g.owner, g.repo, Number(g.graceDays), g.milestones, g.builder || ""], value, cb),
  assignBuilder: (c, a, id, builder, cb) => write(c, a, "assign_builder", [String(id), builder], 0n, cb),
  cancelGrant: (c, a, id, cb) => write(c, a, "cancel_grant", [String(id)], 0n, cb),
  reclaim: (c, a, id, idx, cb) => write(c, a, "reclaim_milestone", [String(id), Number(idx)], 0n, cb),
  apply: (c, a, id, login, pitch, cb) => write(c, a, "apply", [String(id), login, pitch], 0n, cb),
  withdrawApplication: (c, a, id, cb) => write(c, a, "withdraw_application", [String(id)], 0n, cb),
  resign: (c, a, id, cb) => write(c, a, "resign", [String(id)], 0n, cb),
  claim: (c, a, id, idx, cb) => write(c, a, "claim_milestone", [String(id), Number(idx)], 0n, cb),
  verify: (c, a, id, idx, cb) => write(c, a, "verify_milestone", [String(id), Number(idx)], 0n, cb),
};

/** Sum of milestone amounts (wei, bigint) = the exact deposit create_grant expects. */
export function milestoneTotal(milestones) {
  return milestones.reduce((s, m) => s + BigInt(m.amount), 0n);
}

/** What the connected account can do with a milestone right now (mirrors the contract rules). */
export function milestoneActions(grant, m, me, nowS = Math.floor(Date.now() / 1000)) {
  const eq = (x, y) => x && y && x.toLowerCase() === y.toLowerCase();
  const isBuilder = eq(grant.builder, me);
  const isFunder = eq(grant.funder, me);
  return {
    claim: isBuilder && m.status === "OPEN" && nowS <= Number(m.deadline),
    verify: m.status === "CLAIMED" && nowS <= Number(m.reclaimable_after),
    reclaim: isFunder && (m.status === "OPEN" || m.status === "CLAIMED") && nowS > Number(m.reclaimable_after),
  };
}
