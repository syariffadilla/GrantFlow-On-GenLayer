import "./style.css";
import { CONTRACT_ADDRESS } from "./config.js";
import {
  api,
  balanceOf,
  CHAIN,
  genToWei,
  keyClient,
  milestoneActions,
  milestoneTotal,
  newBurnerKey,
  readClient,
  studioFaucet,
  walletClient,
  weiToGen,
} from "./lib/grantflow.js";

// ------------------------------------------------------------------ state

const LS = {
  addr: "grantflow.contract",
  burner: "grantflow.burnerKey",
  mode: "grantflow.accountMode",
};

const state = {
  address: localStorage.getItem(LS.addr) || import.meta.env.VITE_GRANTFLOW_ADDRESS || CONTRACT_ADDRESS,
  rc: readClient(),
  wc: null,
  me: null,
  mode: null,
  stats: null,
  filter: "ALL",
  selected: null, // grant id shown in the detail panel
  draft: null, // milestone rows of the post-grant form
};

// ------------------------------------------------------------------ helpers

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const short = (a) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "");
const gen = (w) => `${weiToGen(w)} GEN`;
const same = (a, b) => a && b && a.toLowerCase() === b.toLowerCase();
const nowS = () => Math.floor(Date.now() / 1000);
const utc = (ts) => (Number(ts) > 0 ? new Date(Number(ts) * 1000).toISOString().replace(".000Z", "Z").replace("T", " ") : "–");
const dateToTs = (d, endOfDay = true) => Math.floor(Date.parse(d + (endOfDay ? "T23:59:59Z" : "T00:00:00Z")) / 1000);
const isoDate = (ts) => new Date(ts * 1000).toISOString().slice(0, 10);
const gh = (g) => `https://github.com/${encodeURIComponent(g.owner)}/${encodeURIComponent(g.repo)}`;

function badge(s) {
  const cls =
    { OPEN: "b-blue", ACTIVE: "b-green", CLOSED: "b-grey", CANCELLED: "b-grey", CLAIMED: "b-amber", PAID: "b-green", RECLAIMED: "b-grey",
      PASS: "b-green", FAIL: "b-red", DEFER: "b-amber", PENDING: "b-amber", ACCEPTED: "b-green", NOT_SELECTED: "b-grey", WITHDRAWN: "b-grey", ENDED: "b-grey" }[s] || "b-grey";
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}

function errMsg(e) {
  return e?.shortMessage || e?.details || e?.message || String(e);
}

// ------------------------------------------------------------------ tx log

function txEntry(label) {
  const box = $("#txlog");
  const el = document.createElement("div");
  el.className = "tx";
  el.innerHTML = `<div><b>${esc(label)}</b></div><div class="muted">waiting for signature…</div>`;
  box.prepend(el);
  box.classList.add("show");
  return {
    update(stage, info) {
      if (stage === "sent")
        el.querySelector(".muted").innerHTML = `sent <code title="${esc(info.hash)}">${short(info.hash)}</code> · waiting for GenLayer consensus…`;
      if (stage === "accepted") {
        el.classList.add(info.ok ? "ok" : "err");
        el.querySelector(".muted").innerHTML =
          `<code title="${esc(info.hash)}">${short(info.hash)}</code> · ${esc(info.status)} · ${esc(info.consensus || "")}` +
          (info.ok ? " · success" : ` · <b>reverted:</b> ${esc(info.error)}`);
      }
    },
    fail(e) {
      el.classList.add("err");
      el.querySelector(".muted").textContent = `failed: ${errMsg(e)}`;
    },
  };
}

/** Run a write with the connected account, show its lifecycle in the tx log, then refresh. */
async function send(label, fn) {
  if (!state.wc) {
    alert("Connect an account first (top right).");
    return null;
  }
  const t = txEntry(label);
  document.body.classList.add("busy");
  try {
    const r = await fn(state.wc, state.address, (stage, info) => t.update(stage, info));
    return r;
  } catch (e) {
    console.error(e);
    t.fail(e);
    return null;
  } finally {
    document.body.classList.remove("busy");
    refreshBalance();
    await renderAll();
  }
}

// ------------------------------------------------------------------ account

function connectBurner() {
  let key = localStorage.getItem(LS.burner);
  if (!key) {
    key = newBurnerKey();
    localStorage.setItem(LS.burner, key);
  }
  const { client, address } = keyClient(key);
  Object.assign(state, { wc: client, me: address, mode: "burner" });
  localStorage.setItem(LS.mode, "burner");
  renderAccount();
  renderAll();
}

async function connectWallet() {
  if (!window.ethereum) {
    alert("No browser wallet found. Use the burner account instead, or install MetaMask.");
    return;
  }
  try {
    const { client, address } = await walletClient(window.ethereum);
    Object.assign(state, { wc: client, me: address, mode: "wallet" });
    localStorage.setItem(LS.mode, "wallet");
    renderAccount();
    renderAll();
  } catch (e) {
    const msg = errMsg(e);
    alert(
      "Wallet connection failed: " + msg +
        (e?.code === 4001 || /reject/i.test(msg)
          ? '\n\nApprove the wallet\'s "Add / switch network: GenLayer Studionet" prompt, or use the burner account.'
          : ""),
    );
  }
}

function disconnect() {
  Object.assign(state, { wc: null, me: null, mode: null });
  localStorage.removeItem(LS.mode);
  renderAccount();
  renderAll();
}

async function refreshBalance() {
  const el = $("#bal");
  if (!el || !state.me) return;
  try {
    el.textContent = gen(await balanceOf(state.rc, state.me));
  } catch {
    el.textContent = "?";
  }
}

async function faucet() {
  try {
    await studioFaucet(state.rc, state.me, 10);
    setTimeout(refreshBalance, 1500);
  } catch (e) {
    alert("Faucet failed: " + errMsg(e));
  }
}

function renderAccount() {
  const box = $("#account");
  if (!state.me) {
    box.innerHTML = `
      <button id="btn-burner" class="primary">Use burner account</button>
      <button id="btn-wallet">Connect MetaMask</button>`;
    $("#btn-burner").onclick = connectBurner;
    $("#btn-wallet").onclick = connectWallet;
    return;
  }
  box.innerHTML = `
    <div class="acct">
      <span class="badge ${state.mode === "burner" ? "b-amber" : "b-blue"}">${state.mode === "burner" ? "burner" : "wallet"}</span>
      <code title="${esc(state.me)}">${short(state.me)}</code>
      <span id="bal" class="muted">…</span>
      <button id="btn-faucet" title="Studio faucet (test GEN, Studionet only)">+10 test GEN</button>
      <button id="btn-out" class="ghost">Disconnect</button>
    </div>`;
  $("#btn-faucet").onclick = faucet;
  $("#btn-out").onclick = disconnect;
  refreshBalance();
}

// ------------------------------------------------------------------ tabs

function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
}

function openGrant(id) {
  state.selected = String(id);
  showTab("grants");
  renderGrants();
  setTimeout(() => $("#grant-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
}

function wireGrantLinks(root) {
  root.querySelectorAll("[data-grant]").forEach((a) =>
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      openGrant(a.dataset.grant);
    }),
  );
}

// ------------------------------------------------------------------ Grants tab

function grantsTable(rows, empty = "No grants yet.") {
  if (!rows.length) return `<p class="muted">${empty}</p>`;
  return `<div class="tablewrap"><table>
    <thead><tr><th>#</th><th>Grant</th><th>Repository</th><th>Funder</th><th>Builder</th><th>Milestones</th><th>Locked</th><th>Paid</th><th>Status</th></tr></thead>
    <tbody>${rows
      .map((g) => {
        const counts = g.milestone_status_counts || {};
        const paid = counts.PAID || 0;
        return `<tr>
          <td>${esc(g.id)}</td>
          <td><a href="#" data-grant="${esc(g.id)}">${esc(g.title)}</a></td>
          <td><a href="${gh(g)}" target="_blank" rel="noopener">${esc(g.owner)}/${esc(g.repo)}</a></td>
          <td><code title="${esc(g.funder)}">${short(g.funder)}</code></td>
          <td>${g.builder ? `<code title="${esc(g.builder)}">${short(g.builder)}</code>` : g.status === "OPEN" ? `<span class="muted">open · ${g.application_count} applied</span>` : `<span class="muted">–</span>`}</td>
          <td>${paid}/${g.milestone_count} paid</td>
          <td>${gen(g.locked)}</td><td>${gen(g.paid)}</td>
          <td>${badge(g.status)}</td></tr>`;
      })
      .join("")}</tbody></table></div>`;
}

async function renderGrants() {
  const el = $("#tab-grants");
  let s;
  let rows;
  try {
    s = await api.stats(state.rc, state.address);
    rows = state.filter === "ALL" ? await api.grants(state.rc, state.address, 0, 50) : await api.grantsByStatus(state.rc, state.address, state.filter, 0, 50);
  } catch (e) {
    el.innerHTML = `<p class="err-text">Could not read the contract at <code>${esc(state.address)}</code>: ${esc(errMsg(e))}</p>
      <p class="muted">Check the contract address under Settings.</p>`;
    return;
  }
  state.stats = s;
  const by = s.grants_by_status;
  el.innerHTML = `
    <div class="hero">
      <h1>Milestone grants that pay out on public GitHub facts.</h1>
      <p>Funders post grants for public repositories and lock the full amount in the contract. Builders apply, the funder assigns one,
      and each milestone pays out only when GenLayer validators each read the GitHub REST API and agree that its criteria are met:
      a release tag published by the deadline, CI checks passing, a pull request merged into a given branch, or a minimum number of commits.
      No one has to approve the payout by hand. If a milestone is not met by its deadline plus a grace period, the funder can take that milestone's GEN back.</p>
    </div>
    <div class="tiles">
      <div class="tile"><span>Grants</span><b>${s.grant_count}</b></div>
      <div class="tile"><span>Open / active</span><b>${by.OPEN} / ${by.ACTIVE}</b></div>
      <div class="tile"><span>Locked in escrow</span><b>${gen(s.total_locked)}</b></div>
      <div class="tile"><span>Paid to builders</span><b>${gen(s.total_paid)}</b></div>
      <div class="tile"><span>Refunded to funders</span><b>${gen(s.total_refunded)}</b></div>
      <div class="tile"><span>Verifications pass / fail / defer</span><b>${s.verify_pass} / ${s.verify_fail} / ${s.verify_defer}</b></div>
    </div>
    <div class="row between">
      <h2>Grants</h2>
      <div class="filters">${["ALL", "OPEN", "ACTIVE", "CLOSED", "CANCELLED"]
        .map((f) => `<button class="${state.filter === f ? "primary" : "ghost"}" data-filter="${f}">${f.toLowerCase()}</button>`)
        .join("")}</div>
    </div>
    ${grantsTable(rows)}
    <p class="muted small">Read live from the contract with <code>genlayer-js</code> (<code>get_stats</code>, <code>list_grants</code>, <code>list_grants_by_status</code>). Newest first, up to 50.</p>
    <div id="grant-detail"></div>`;
  el.querySelectorAll("[data-filter]").forEach((b) => (b.onclick = () => ((state.filter = b.dataset.filter), renderGrants())));
  wireGrantLinks(el);
  if (state.selected) renderDetail(state.selected);
}

function criteriaList(g, m) {
  const items = [`release <a href="${gh(g)}/releases/tag/${encodeURIComponent(m.tag)}" target="_blank" rel="noopener"><code>${esc(m.tag)}</code></a> published by the deadline` +
    (Number(m.not_before) > 0 ? ` and not before ${utc(m.not_before)}` : "")];
  if (m.checks === "check_runs" || m.checks === "both")
    items.push(m.required_checks.length ? `check runs passing: ${m.required_checks.map((c) => `<code>${esc(c)}</code>`).join(", ")}` : "every check run on the tagged commit passing");
  if (m.checks === "status" || m.checks === "both") items.push("combined commit status <code>success</code>");
  if (Number(m.pr_number) > 0)
    items.push(`PR <a href="${gh(g)}/pull/${m.pr_number}" target="_blank" rel="noopener">#${m.pr_number}</a> merged into <code>${esc(m.pr_base)}</code> by the deadline`);
  if (m.commit_author)
    items.push(`≥ ${m.min_commits} commits by <code>${esc(m.commit_author)}</code> between ${utc(m.commits_since)} and ${utc(m.commits_until)}`);
  return `<ul class="crit">${items.map((i) => `<li>${i}</li>`).join("")}</ul>`;
}

function evidenceView(g, m) {
  const evJson = m.evidence;
  if (!evJson) return "";
  let ev;
  try {
    ev = JSON.parse(evJson);
  } catch {
    return `<pre>${esc(evJson)}</pre>`;
  }
  const failing = (ev.check_runs || []).filter((r) => r[1] !== "completed" || !["success", "neutral", "skipped"].includes(r[2]));
  const rows = [
    ["Verdict", `${badge(ev.verdict)} ${esc(ev.reason)}`],
    ["Payout", gen(ev.payout)],
    ["Release", ev.release_http ? `HTTP ${ev.release_http} · tag <code>${esc(ev.release_tag)}</code> · published ${esc(ev.release_published_at || "–")}${ev.release_draft ? " · draft" : ""}` : "not fetched"],
    ["Tagged commit", ev.commit_sha ? `<a href="${gh(g)}/commit/${esc(ev.commit_sha)}" target="_blank" rel="noopener"><code>${esc(ev.commit_sha.slice(0, 12))}</code></a>` : "–"],
  ];
  if (ev.check_runs_http) rows.push(["Check runs", `${ev.check_runs_total} latest · ${failing.length ? "not passing: " + failing.map((r) => `<code>${esc(r[0])}</code> (${esc(r[2] || r[1])})`).join(", ") : "all passing"}` +
      (m.required_checks.length ? ` <span class="muted">(only the ${m.required_checks.length} required checks count for this milestone)</span>` : "")]);
  if (ev.status_http) rows.push(["Commit status", `${esc(ev.status_state)} (${ev.status_total} context${ev.status_total === 1 ? "" : "s"})`]);
  if (ev.pr_http) rows.push(["Pull request", `#${ev.pr_number} · ${ev.pr_merged ? "merged " + esc(ev.pr_merged_at) : "not merged"} · base <code>${esc(ev.pr_base)}</code>`]);
  if (ev.commits_http) rows.push(["Commits", `${ev.commits_count} by <code>${esc(ev.commit_author)}</code>`]);
  if (ev.fetch_error) rows.push(["Fetch error", `<code>${esc(ev.fetch_error)}</code>`]);
  return `<details class="evidence"><summary>Evidence agreed by validators (last verification)</summary>
    <div class="kv">${rows.map(([k, v]) => `<div>${k}</div><div>${v}</div>`).join("")}</div>
    <details><summary class="muted small">raw evidence JSON</summary><pre>${esc(JSON.stringify(ev, null, 1))}</pre></details></details>`;
}

async function renderDetail(id) {
  const box = $("#grant-detail");
  if (!box) return;
  let g;
  let apps;
  try {
    [g, apps] = await Promise.all([api.grant(state.rc, state.address, id), api.grantApplications(state.rc, state.address, id)]);
  } catch (e) {
    box.innerHTML = `<p class="err-text">${esc(errMsg(e))}</p>`;
    return;
  }
  const me = state.me;
  const isFunder = same(g.funder, me);
  const isBuilder = same(g.builder, me);
  const myPending = apps.find((a) => same(a.builder, me) && a.status === "PENDING");
  const roleNote = isFunder ? "You are the funder." : isBuilder ? "You are the assigned builder." : me ? "" : "Connect an account to act on this grant.";

  const msHtml = g.milestones
    .map((m) => {
      const act = me ? milestoneActions(g, m, me) : { claim: false, verify: m.status === "CLAIMED" && nowS() <= Number(m.reclaimable_after), reclaim: false };
      const btns = [
        act.claim ? `<button class="primary" data-act="claim" data-i="${m.index}">Claim milestone</button>` : "",
        act.verify ? `<button class="primary" data-act="verify" data-i="${m.index}" title="Anyone can call this">Verify on GitHub</button>` : "",
        act.reclaim ? `<button data-act="reclaim" data-i="${m.index}">Reclaim ${gen(m.amount)}</button>` : "",
      ].join(" ");
      return `<div class="card ms">
        <div class="row between"><b>#${m.index} ${esc(m.label || "Milestone")}</b><span>${badge(m.status)} ${m.last_verdict ? badge(m.last_verdict) : ""}</span></div>
        <div class="muted small">${gen(m.amount)} · deadline ${utc(m.deadline)} · verify until / reclaim after ${utc(m.reclaimable_after)} · claims ${m.claims} · verifications ${m.verify_attempts}${m.paid_to ? ` · paid to <code>${short(m.paid_to)}</code>` : ""}</div>
        ${criteriaList(g, m)}
        ${m.last_reason ? `<p class="small">Last result: ${badge(m.last_verdict)} <code>${esc(m.last_reason)}</code></p>` : ""}
        ${evidenceView(g, m)}
        <div class="actions">${btns}</div>
      </div>`;
    })
    .join("");

  const appsHtml = apps.length
    ? `<div class="tablewrap"><table><thead><tr><th>Applicant</th><th>GitHub login (self-reported)</th><th>Pitch</th><th>Status</th>${isFunder && g.status === "OPEN" ? "<th></th>" : ""}</tr></thead><tbody>${apps
        .map(
          (a) => `<tr><td><code title="${esc(a.builder)}">${short(a.builder)}</code></td>
            <td><a href="https://github.com/${encodeURIComponent(a.github_login)}" target="_blank" rel="noopener">${esc(a.github_login)}</a></td>
            <td>${esc(a.pitch)}</td><td>${badge(a.status)}</td>
            ${isFunder && g.status === "OPEN" ? `<td>${a.status === "PENDING" ? `<button class="primary" data-assign="${esc(a.builder)}">Assign</button>` : ""}</td>` : ""}</tr>`,
        )
        .join("")}</tbody></table></div>`
    : `<p class="muted">No applications yet.</p>`;

  const canApply = me && g.status === "OPEN" && !isFunder && !myPending;
  box.innerHTML = `
    <div class="card detail">
      <div class="row between"><h2>Grant #${esc(g.id)} · ${esc(g.title)}</h2><span>${badge(g.status)}</span></div>
      <p>${esc(g.description) || '<span class="muted">No description.</span>'}</p>
      <div class="kv">
        <div>Repository</div><div><a href="${gh(g)}" target="_blank" rel="noopener">github.com/${esc(g.owner)}/${esc(g.repo)}</a></div>
        <div>Funder</div><div><code>${esc(g.funder)}</code></div>
        <div>Builder</div><div>${g.builder ? `<code>${esc(g.builder)}</code> (assigned ${utc(g.assigned_at)})` : "not assigned · open for applications"}</div>
        <div>Escrow</div><div>${gen(g.deposited)} deposited · ${gen(g.paid)} paid · ${gen(g.refunded)} refunded · <b>${gen(g.locked)} locked</b></div>
        <div>Grace period</div><div>${Math.round(Number(g.grace_seconds) / 86400)} days after each deadline</div>
      </div>
      <p class="muted small">${esc(roleNote)}</p>
      <div class="actions">
        ${isFunder && g.status === "OPEN" ? `<button id="btn-cancel">Cancel grant and refund open milestones</button>` : ""}
        ${isBuilder && g.status === "ACTIVE" ? `<button id="btn-resign" class="ghost">Resign as builder</button>` : ""}
        ${myPending ? `<button id="btn-withdraw" class="ghost">Withdraw my application</button>` : ""}
      </div>
      <h3>Milestones</h3>${msHtml}
      <h3>Applications</h3>${appsHtml}
      ${canApply ? `<form id="apply-form" class="card"><h3>Apply as builder</h3>
        <div class="row"><label>Your GitHub login (shown to the funder, not verified on-chain)<input name="login" required maxlength="39" /></label></div>
        <label>Pitch (max 400 chars)<textarea name="pitch" maxlength="400" required rows="3"></textarea></label>
        <button class="primary" type="submit">Apply</button></form>` : ""}
    </div>`;

  box.querySelectorAll("[data-act]").forEach((b) => {
    const i = Number(b.dataset.i);
    b.onclick = () => {
      if (b.dataset.act === "claim") send(`claim_milestone #${g.id}/${i}`, (c, a, cb) => api.claim(c, a, g.id, i, cb));
      if (b.dataset.act === "verify")
        send(`verify_milestone #${g.id}/${i} (validators read GitHub)`, (c, a, cb) => api.verify(c, a, g.id, i, cb));
      if (b.dataset.act === "reclaim") send(`reclaim_milestone #${g.id}/${i}`, (c, a, cb) => api.reclaim(c, a, g.id, i, cb));
    };
  });
  box.querySelectorAll("[data-assign]").forEach((b) => {
    b.onclick = () => send(`assign_builder #${g.id}`, (c, a, cb) => api.assignBuilder(c, a, g.id, b.dataset.assign, cb));
  });
  $("#btn-cancel", box) && ($("#btn-cancel", box).onclick = () => confirm("Cancel this grant? Every OPEN milestone is refunded to you.") && send(`cancel_grant #${g.id}`, (c, a, cb) => api.cancelGrant(c, a, g.id, cb)));
  $("#btn-resign", box) && ($("#btn-resign", box).onclick = () => confirm("Resign? The grant re-opens for applications.") && send(`resign #${g.id}`, (c, a, cb) => api.resign(c, a, g.id, cb)));
  $("#btn-withdraw", box) && ($("#btn-withdraw", box).onclick = () => send(`withdraw_application #${g.id}`, (c, a, cb) => api.withdrawApplication(c, a, g.id, cb)));
  const af = $("#apply-form", box);
  if (af)
    af.onsubmit = (ev) => {
      ev.preventDefault();
      const f = new FormData(af);
      send(`apply #${g.id}`, (c, a, cb) => api.apply(c, a, g.id, String(f.get("login")).trim(), String(f.get("pitch")).trim(), cb));
    };
}

// ------------------------------------------------------------------ Funder tab

function blankRow() {
  return { label: "", amount: "1", deadline: isoDate(nowS() + 30 * 86400), tag: "", checks: "none", required: "", pr: "", base: "", author: "", min: "", since: "", until: "" };
}

const EXAMPLE = {
  title: "Flask 3.1.3 release (example on public data)",
  description: "Example grant: pays the builder if pallets/flask shows release 3.1.3 with green required CI and PR #5924 merged into stable.",
  owner: "pallets",
  repo: "flask",
  grace: "7",
  rows: [
    { label: "Release 3.1.3 + required CI + PR #5924", amount: "1", deadline: isoDate(nowS() + 30 * 86400), tag: "3.1.3", checks: "check_runs", required: "build, typing, 3.12, 3.13, Windows, Mac", pr: "5924", base: "stable", author: "", min: "", since: "", until: "" },
  ],
};

function readForm(form) {
  const f = new FormData(form);
  const rows = state.draft.rows.map((_, i) => ({
    label: f.get(`label${i}`), amount: f.get(`amount${i}`), deadline: f.get(`deadline${i}`), tag: f.get(`tag${i}`), checks: f.get(`checks${i}`),
    required: f.get(`required${i}`), pr: f.get(`pr${i}`), base: f.get(`base${i}`), author: f.get(`author${i}`), min: f.get(`min${i}`),
    since: f.get(`since${i}`), until: f.get(`until${i}`),
  }));
  return { title: f.get("title"), description: f.get("description"), owner: f.get("owner"), repo: f.get("repo"), grace: f.get("grace"), builder: f.get("builder"), rows };
}

function toSpec(r) {
  const spec = { label: String(r.label || "").trim(), amount: genToWei(r.amount), deadline: dateToTs(r.deadline), tag: String(r.tag).trim(), checks: r.checks };
  const req = String(r.required || "").split(",").map((x) => x.trim()).filter(Boolean);
  if (req.length) spec.required_checks = req;
  if (String(r.pr).trim()) {
    spec.pr_number = Number(r.pr);
    spec.pr_base = String(r.base).trim();
  }
  if (String(r.author).trim()) {
    spec.commit_author = String(r.author).trim();
    spec.min_commits = Number(r.min);
    spec.commits_since = dateToTs(r.since, false);
    spec.commits_until = dateToTs(r.until);
  }
  return spec;
}

async function renderFunder() {
  const el = $("#tab-funder");
  if (!state.draft) state.draft = { title: "", description: "", owner: "", repo: "", grace: "7", builder: "", rows: [blankRow()] };
  const d = state.draft;
  const rowHtml = (r, i) => `
    <fieldset class="card ms"><legend>Milestone #${i}</legend>
      <div class="row"><label>Label<input name="label${i}" value="${esc(r.label)}" maxlength="80" /></label>
        <label class="narrow">Amount (GEN)<input name="amount${i}" value="${esc(r.amount)}" required /></label>
        <label class="narrow">Deadline (UTC, end of day)<input type="date" name="deadline${i}" value="${esc(r.deadline)}" required /></label></div>
      <div class="row"><label class="narrow">Release tag<input name="tag${i}" value="${esc(r.tag)}" required placeholder="v1.0.0" /></label>
        <label class="narrow">CI checks<select name="checks${i}">${["none", "check_runs", "status", "both"].map((c) => `<option ${c === r.checks ? "selected" : ""}>${c}</option>`).join("")}</select></label>
        <label>Required check-run names (comma separated, empty = all)<input name="required${i}" value="${esc(r.required)}" /></label></div>
      <div class="row"><label class="narrow">PR number (optional)<input name="pr${i}" value="${esc(r.pr)}" /></label>
        <label class="narrow">PR base branch<input name="base${i}" value="${esc(r.base)}" placeholder="main" /></label>
        <label class="narrow">Commit author login (optional)<input name="author${i}" value="${esc(r.author)}" /></label>
        <label class="narrow">Min commits<input name="min${i}" value="${esc(r.min)}" /></label>
        <label class="narrow">From<input type="date" name="since${i}" value="${esc(r.since)}" /></label>
        <label class="narrow">To<input type="date" name="until${i}" value="${esc(r.until)}" /></label></div>
      ${d.rows.length > 1 ? `<button type="button" class="ghost" data-del="${i}">Remove milestone</button>` : ""}
    </fieldset>`;
  let total = "?";
  try {
    total = gen(milestoneTotal(d.rows.map((r) => ({ amount: genToWei(r.amount) }))));
  } catch {}
  el.innerHTML = `
    <form id="grant-form">
      <div class="row between"><h2>Post a grant</h2><button type="button" id="btn-example" class="ghost">Fill example (pallets/flask 3.1.3)</button></div>
      <div class="row"><label>Title<input name="title" value="${esc(d.title)}" required minlength="3" maxlength="80" /></label></div>
      <label>Description (max 600 chars)<textarea name="description" maxlength="600" rows="2">${esc(d.description)}</textarea></label>
      <div class="row"><label class="narrow">GitHub owner<input name="owner" value="${esc(d.owner)}" required placeholder="pallets" /></label>
        <label class="narrow">Repository<input name="repo" value="${esc(d.repo)}" required placeholder="flask" /></label>
        <label class="narrow">Grace period (days, 1-90)<input name="grace" value="${esc(d.grace)}" required /></label>
        <label>Builder address (optional; empty = open for applications)<input name="builder" value="${esc(d.builder)}" placeholder="0x…" /></label></div>
      ${d.rows.map(rowHtml).join("")}
      <div class="actions"><button type="button" id="btn-add" class="ghost">+ Add milestone</button>
      <button class="primary" type="submit">Post grant and deposit ${total}</button></div>
      <p class="muted small">The deposit must equal the sum of milestone amounts; the contract rejects any other amount. Criteria are checked against the public,
      unauthenticated GitHub REST API, so the repository must be public. Once a builder is assigned you can no longer cancel the grant; unpaid milestones
      can be reclaimed after their deadline plus the grace period.</p>
      <div id="grant-err" class="err-text"></div>
    </form>
    <h2>My funded grants</h2><div id="my-funded"><p class="muted">${state.me ? "Loading…" : "Connect an account to see your grants."}</p></div>`;

  const form = $("#grant-form");
  form.oninput = () => {
    state.draft = readForm(form);
    let t = "?";
    try {
      t = gen(milestoneTotal(state.draft.rows.map((r) => ({ amount: genToWei(r.amount) }))));
    } catch {}
    $("#grant-form button[type=submit]").textContent = `Post grant and deposit ${t}`;
  };
  $("#btn-add").onclick = () => {
    state.draft = readForm(form);
    state.draft.rows.push(blankRow());
    renderFunder();
  };
  $("#btn-example").onclick = () => {
    state.draft = JSON.parse(JSON.stringify({ ...EXAMPLE, builder: "" }));
    renderFunder();
  };
  el.querySelectorAll("[data-del]").forEach(
    (b) =>
      (b.onclick = () => {
        state.draft = readForm(form);
        state.draft.rows.splice(Number(b.dataset.del), 1);
        renderFunder();
      }),
  );
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    $("#grant-err").textContent = "";
    const d2 = readForm(form);
    let spec;
    try {
      spec = {
        title: String(d2.title).trim(),
        description: String(d2.description || "").trim(),
        owner: String(d2.owner).trim(),
        repo: String(d2.repo).trim(),
        graceDays: Number(d2.grace),
        builder: String(d2.builder || "").trim(),
        milestones: d2.rows.map(toSpec),
      };
    } catch (e) {
      $("#grant-err").textContent = errMsg(e);
      return;
    }
    const value = milestoneTotal(spec.milestones);
    const r = await send(`create_grant "${spec.title}" (${gen(value)})`, (c, a, cb) => api.createGrant(c, a, spec, value, cb));
    if (r?.ok) {
      state.draft = null;
      openGrant(String(r.returned ?? "").replaceAll('"', ""));
    }
  };

  if (state.me) {
    try {
      const mine = await api.funderGrants(state.rc, state.address, state.me);
      $("#my-funded").innerHTML = grantsTable(mine.reverse(), "You have not posted a grant yet.");
      wireGrantLinks($("#my-funded"));
    } catch (e) {
      $("#my-funded").innerHTML = `<p class="err-text">${esc(errMsg(e))}</p>`;
    }
  }
}

// ------------------------------------------------------------------ Builder tab

async function renderBuilder() {
  const el = $("#tab-builder");
  el.innerHTML = `
    <h2>Open grants</h2><div id="b-open"><p class="muted">Loading…</p></div>
    <h2>Grants assigned to me</h2><div id="b-mine"><p class="muted">${state.me ? "Loading…" : "Connect an account."}</p></div>
    <h2>My applications</h2><div id="b-apps"><p class="muted">${state.me ? "Loading…" : "Connect an account."}</p></div>
    <p class="muted small">Workflow: apply to an open grant → the funder assigns you → when a milestone is done on GitHub, claim it → press
    <b>Verify on GitHub</b> (anyone can). Validators each read the GitHub API; on PASS the milestone amount is sent to the assigned builder's address.</p>`;
  try {
    const open = await api.grantsByStatus(state.rc, state.address, "OPEN", 0, 50);
    $("#b-open").innerHTML = grantsTable(open, "No grants are open for applications right now.");
    wireGrantLinks($("#b-open"));
  } catch (e) {
    $("#b-open").innerHTML = `<p class="err-text">${esc(errMsg(e))}</p>`;
  }
  if (!state.me) return;
  try {
    const [mine, apps] = await Promise.all([api.builderGrants(state.rc, state.address, state.me), api.builderApplications(state.rc, state.address, state.me)]);
    const current = mine.filter((g) => same(g.builder, state.me));
    $("#b-mine").innerHTML = grantsTable(current.reverse(), "No grant is assigned to you.");
    wireGrantLinks($("#b-mine"));
    $("#b-apps").innerHTML = apps.length
      ? `<div class="tablewrap"><table><thead><tr><th>Grant</th><th>GitHub login</th><th>Pitch</th><th>Status</th></tr></thead><tbody>${apps
          .reverse()
          .map((a) => `<tr><td><a href="#" data-grant="${esc(a.grant_id)}">#${esc(a.grant_id)}</a></td><td>${esc(a.github_login)}</td><td>${esc(a.pitch)}</td><td>${badge(a.status)}</td></tr>`)
          .join("")}</tbody></table></div>`
      : `<p class="muted">No applications yet.</p>`;
    wireGrantLinks($("#b-apps"));
  } catch (e) {
    $("#b-mine").innerHTML = `<p class="err-text">${esc(errMsg(e))}</p>`;
  }
}

// ------------------------------------------------------------------ Settings tab

function renderSettings() {
  const el = $("#tab-settings");
  el.innerHTML = `
    <div class="grid2">
      <form id="addr-form" class="card"><h2>Contract</h2>
        <label>GrantFlow contract address (GenLayer Studionet)<input name="addr" value="${esc(state.address)}" /></label>
        <div class="actions"><button class="primary" type="submit">Use this address</button>
        <button type="button" id="btn-reset" class="ghost">Reset to default</button></div>
        <p class="muted small">Default: <code>${esc(import.meta.env.VITE_GRANTFLOW_ADDRESS || CONTRACT_ADDRESS)}</code> ·
        <a href="https://explorer-studio.genlayer.com/address/${esc(state.address)}" target="_blank" rel="noopener">open in the Studio explorer</a></p>
      </form>
      <div class="card"><h2>Account</h2>
        <p>${state.me ? `Connected as <code>${esc(state.me)}</code> (${esc(state.mode)})` : "Not connected."}</p>
        <p class="muted small">Network: ${esc(CHAIN.name)} (chain id ${CHAIN.id}) · RPC <code>${esc(CHAIN.rpcUrls.default.http[0])}</code></p>
        <p class="muted small"><b>Burner account:</b> a private key generated in this browser and kept in localStorage. Use it only for Studionet test GEN.
        <b>MetaMask:</b> the app asks the wallet to add or switch to GenLayer Studionet with the standard <code>wallet_addEthereumChain</code> /
        <code>wallet_switchEthereumChain</code> requests (no Snap needed). The faucet button calls Studio's <code>sim_fundAccount</code> and only works on Studionet.</p>
        ${state.mode === "burner" ? `<button id="btn-forget" class="ghost">Delete burner key from this browser</button>` : ""}
      </div>
    </div>
    <div class="card"><h2>How verification works</h2>
      <p class="small">Calling <code>verify_milestone</code> starts a non-deterministic GenLayer transaction. The leader fetches the release, the tagged commit,
      its check runs and/or combined status, the pull request and the commit list from <code>api.github.com</code> (unauthenticated) and builds an evidence record.
      Every validator fetches the same URLs itself and votes to agree only if all 35 evidence fields are identical to its own and the verdict follows from them.
      After consensus the contract re-checks the evidence against the milestone's stored criteria before any GEN moves. PASS pays the assigned builder once;
      FAIL re-opens the milestone so the builder can fix it and claim again before the deadline; DEFER (GitHub error, 404, rate limit, pending CI) moves nothing.</p>
      <p class="muted small">Limitations: GitHub data can change (re-run checks, edited releases), and GitHub's unauthenticated rate limit can cause DEFER.
      GitHub logins in applications are self-reported. This is a Studionet prototype with test GEN only.</p>
    </div>`;
  $("#addr-form").onsubmit = (ev) => {
    ev.preventDefault();
    const v = String(new FormData(ev.target).get("addr")).trim();
    if (!/^0x[0-9a-fA-F]{40}$/.test(v)) return alert("Not an address");
    state.address = v;
    localStorage.setItem(LS.addr, v);
    state.selected = null;
    renderAll();
  };
  $("#btn-reset").onclick = () => {
    localStorage.removeItem(LS.addr);
    state.address = import.meta.env.VITE_GRANTFLOW_ADDRESS || CONTRACT_ADDRESS;
    state.selected = null;
    renderAll();
  };
  const fb = $("#btn-forget");
  if (fb)
    fb.onclick = () => {
      if (!confirm("Delete the burner key? Any test GEN on it becomes unreachable.")) return;
      localStorage.removeItem(LS.burner);
      disconnect();
    };
}

// ------------------------------------------------------------------ boot

async function renderAll() {
  $("#footer-addr").textContent = state.address;
  renderSettings();
  await Promise.all([renderGrants(), renderFunder(), renderBuilder()]);
}

document.querySelectorAll("#tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
if (localStorage.getItem(LS.mode) === "burner" && localStorage.getItem(LS.burner)) connectBurner();
else {
  renderAccount();
  renderAll();
}
