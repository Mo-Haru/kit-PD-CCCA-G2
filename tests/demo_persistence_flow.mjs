// Real browser crypto/client + real Flask APIs/SQLite, with a software authenticator.
import assert from "node:assert/strict";
import { createInterface } from "node:readline";
import { b64urlToBytes, bytesToB64url } from "../app/static/js/watashi/bytes.mjs";
import { entriesFromPayload, plaintextForSelection, parseShareFragment } from "../app/static/js/watashi/crypto.mjs";
import { createDemoAnswerStore } from "../app/static/js/watashi/answer-demo-store.mjs";
import { applyDemoItems, demoAnswerItems, fillSampleAnswers } from "../app/static/js/watashi/answer-demo.mjs";

const input = createInterface({ input: process.stdin });
const lines = input[Symbol.asyncIterator]();
let actor = "A";
let stage = "setup";
const clients = {}, cookies = {}, prfs = {}, accounts = {};
const privateMarkers = [];
async function rpc(kind, data = {}) {
  process.stdout.write(JSON.stringify({ kind, actor, ...data }) + "\n");
  const reply = await lines.next();
  if (reply.done) throw new Error("Bridge closed");
  return JSON.parse(reply.value);
}
globalThis.document = { get cookie() { return `wn_csrf=${cookies[actor] || ""}`; }, querySelector() { return null; } };
globalThis.location = { origin: "http://localhost" };
globalThis.PublicKeyCredential = { async getClientCapabilities() { return { prf: true }; } };
globalThis.fetch = async (path, options = {}) => {
  const reply = await rpc("api", { path, method: options.method || "GET", body: options.body || null,
    headers: Object.fromEntries(options.headers || []) });
  cookies[actor] = reply.csrf;
  return new Response(JSON.stringify(reply.payload), { status: reply.status });
};
async function credential(operation, { publicKey }) {
  const wire = { ...publicKey, challenge: bytesToB64url(publicKey.challenge) };
  if (wire.user) wire.user = { ...wire.user, id: bytesToB64url(wire.user.id) };
  for (const name of ["allowCredentials", "excludeCredentials"]) {
    if (wire[name]) wire[name] = wire[name].map(item => ({ ...item, id: bytesToB64url(item.id) }));
  }
  delete wire.extensions;
  const result = await rpc("credential", { operation, public_key: wire });
  const response = Object.fromEntries(Object.entries(result.response)
    .filter(([key]) => key !== "transports").map(([key, value]) => [key, b64urlToBytes(value)]));
  response.getTransports = () => ["internal"];
  return { id: result.id, rawId: b64urlToBytes(result.rawId), type: "public-key", response,
    getClientExtensionResults() { return { prf: { results: { first: prfs[actor] } } }; } };
}
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { credentials: {
  create: options => credential("register", options), get: options => credential("authenticate", options),
} } });
function use(name) { actor = name; return clients[name]; }
async function denied(client, path, code, options) {
  await assert.rejects(client.api(path, options), error => error.status === code);
}
function fixtureForm(fixture) {
  const questions = fixture.questions.map(question => {
    const choice = ["single_choice", "multiple_choice"].includes(question.input_type);
    const controls = (choice ? question.options : [{}]).map(option => ({
      tagName: question.input_type === "select" ? "SELECT" : "INPUT",
      type: choice ? question.input_type === "single_choice" ? "radio" : "checkbox" : "text",
      value: "", checked: false,
      dataset: { answerLabel: option.text, demoSample: choice ? String(Array.isArray(question.sample)
        ? question.sample.includes(option.value) : question.sample === option.value) : question.sample },
      options: (question.options || []).map(option => ({ ...option, textContent: option.text })),
      get selectedOptions() { return this.options.filter(option => option.value === this.value); },
    }));
    return { dataset: { questionId: question.id, questionTitle: question.text }, querySelectorAll: () => controls };
  });
  return { querySelectorAll: selector => selector === "[data-demo-question]" ? questions : questions.flatMap(q => q.querySelectorAll()) };
}
try {
  const { fixture } = await rpc("fixture");
  for (const name of ["A", "B", "C", "viewer", "other-viewer"]) {
    actor = name;
    clients[name] = await import(`../app/static/js/watashi/client.mjs?persist-test=${name}`);
    if (["viewer", "other-viewer"].includes(name)) continue;
    prfs[name] = crypto.getRandomValues(new Uint8Array(32));
    accounts[name] = await clients[name].registerAccount(`保存共有テスト${name}`);
    await clients[name].logout();
    await clients[name].login();
    await clients[name].unlock();
    privateMarkers.push(bytesToB64url(prfs[name]), bytesToB64url(clients[name].unlockedKeyMaterial().storageKey));
  }
  const a = use("A");
  const catalog = await a.api("/api/v1/answers/questions");
  const ids = fixture.questions.map(question => question.id);
  assert.ok(ids.every(id => catalog.questions.some(question => question.id === id)));
  const existing = { "historical-answer": { state: "declined" } };
  await a.saveAnswerDocument(existing, 0);
  stage = "fixture serialize / encrypted merge / reload";
  const form = fixtureForm(fixture);
  fillSampleAnswers(form);
  const edited = demoAnswerItems(form);
  assert.equal(Object.keys(edited).length, 6);
  const store = createDemoAnswerStore(a, ids);
  assert.equal((await store.load()).revision, 1);
  assert.equal((await store.save(edited)).revision, 2);
  const restored = await store.load();
  assert.deepEqual(restored.items, { ...existing, ...edited });
  const restoredForm = fixtureForm(fixture);
  applyDemoItems(restoredForm, restored.items);
  assert.deepEqual(demoAnswerItems(restoredForm), edited);
  stage = "revision conflict preserves draft and unrelated answers";
  const stale = createDemoAnswerStore(a, ids);
  await stale.load();
  const changed = structuredClone(edited);
  changed[ids[0]].value = "更新用のダミー回答文字列";
  await store.save(changed);
  await assert.rejects(stale.save(edited), error => error.code === "revision_conflict");
  assert.equal(stale.revision, 2);
  assert.deepEqual((await a.loadAnswerDocument()).document.items, { ...existing, ...changed });

  stage = "family invite / selected encrypted shares / recipient decryption";
  const invitation = await a.api("/api/v1/family/invitations", { method: "POST", body: { kind: "open" } });
  await use("B").api("/api/v1/family/invitations/request", { method: "POST", body: { token: invitation.token } });
  const connection = await use("A").api(`/api/v1/family/invitations/${invitation.invitation.id}/confirm`, { method: "POST", body: {} });
  const context = { connectionId: connection.connection_id, recipientUserId: accounts.B.user_id, recipientKeyId: accounts.B.encryption_key_id };
  const saved = await a.loadAnswerDocument();
  const shares = [];
  for (const selection of [{ scope: "all" }, { scope: "chapters", ids: [fixture.categories[0].id] }, { scope: "questions", ids: [ids[0], ids[2]] }]) {
    use("A");
    const payload = plaintextForSelection(saved.document, catalog, selection);
    const { share } = await a.createFamilyShare({ ...context, selection, payload });
    const received = await use("B").api(`/api/v1/family/received/${share.id}`);
    assert.deepEqual(await clients.B.openReceivedShare(received.share), payload);
    shares.push({ share, payload });
  }
  assert.deepEqual(shares.map(item => Object.keys(item.payload.items).length), [6, 3, 2]);
  stage = "answer save does not alter existing share; explicit shared update does";
  use("A");
  changed[ids[0]].value = "再更新用のダミー回答文字列";
  await store.save(changed);
  let received = await use("B").api(`/api/v1/family/received/${shares[0].share.id}`);
  assert.deepEqual(await clients.B.openReceivedShare(received.share), shares[0].payload);
  use("A");
  const latest = await a.loadAnswerDocument();
  const nextPayload = plaintextForSelection(latest.document, catalog, { scope: "all" });
  await a.updateFamilyShare({ ...context, shareId: shares[0].share.id, expectedVersion: 1, selection: { scope: "all" }, payload: nextPayload });
  received = await use("B").api(`/api/v1/family/received/${shares[0].share.id}`);
  assert.equal(received.share.current_version, 2);
  assert.deepEqual(await clients.B.openReceivedShare(received.share), nextPayload);
  await denied(use("C"), `/api/v1/family/received/${shares[0].share.id}`, 404);
  await denied(use("viewer"), `/api/v1/family/received/${shares[0].share.id}`, 401);
  await use("A").api(`/api/v1/family/shares/${shares[0].share.id}/stop`, { method: "POST", body: {} });
  await denied(use("B"), `/api/v1/family/received/${shares[0].share.id}`, 404);

  stage = "shared link / confirmed snapshot / viewer decryption / stop";
  use("A");
  const selection = { scope: "questions", ids: [ids[0], ids[2]] };
  const confirmed = plaintextForSelection(latest.document, catalog, selection);
  // Verify createQrShare uses the confirmed bytes even after the answer is saved again.
  changed[ids[0]].value = "確認後のダミー回答変更文字列";
  await store.save(changed);
  const clock = await rpc("clock", { advance: 0 });
  const expiresAt = new Date(Date.parse(clock.now) + 60000).toISOString();
  const created = await a.createQrShare({ mode: "timed", selection, expiresAt, payload: confirmed });
  const secrets = parseShareFragment(new URL(created.url).hash);
  const { resumeOrStartQr, openSharedPayload } = await import("../app/static/js/watashi/view.mjs");
  const viewer = use("viewer");
  await denied(viewer, "/api/v1/answers/current", 401);
  const viewed = await resumeOrStartQr({ api: viewer.api, shareId: created.share.id,
    secrets: { ...secrets, attempt: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))) }, resumeOnly: false });
  const opened = await openSharedPayload(secrets.privateKey, viewed.current, created.share.id);
  assert.deepEqual(opened, confirmed);
  assert.equal(entriesFromPayload(opened, catalog)[0].label, fixture.questions[0].text);
  use("A");
  await a.api(`/api/v1/qr-shares/${created.share.id}/stop`, { method: "POST", body: {} });
  await assert.rejects(use("viewer").api("/api/v1/qr-view/current"), error => error.code === "qr_stopped");

  stage = "emergency link is single-use; timed link expiry";
  use("A");
  const emergency = await a.createQrShare({ mode: "emergency", selection, payload: confirmed });
  const emergencySecrets = parseShareFragment(new URL(emergency.url).hash);
  const body = { start_token: bytesToB64url(emergencySecrets.startToken), attempt_id: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))) };
  await use("viewer").api(`/api/v1/qr-shares/${emergency.share.id}/start`, { method: "POST", body });
  await denied(use("other-viewer"), `/api/v1/qr-shares/${emergency.share.id}/start`, 409,
    { method: "POST", body: { ...body, attempt_id: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))) } });
  use("A");
  const short = await a.createQrShare({ mode: "timed", selection, payload: confirmed,
    expiresAt: new Date(Date.parse(clock.now) + 1000).toISOString() });
  await rpc("clock", { advance: 2 });
  const shortSecrets = parseShareFragment(new URL(short.url).hash);
  await assert.rejects(use("other-viewer").api(`/api/v1/qr-shares/${short.share.id}/start`, { method: "POST", body: {
    start_token: bytesToB64url(shortSecrets.startToken), attempt_id: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))),
  } }), error => error.code === "qr_expired");

  stage = "database and HTTP plaintext / key checks";
  const evidence = await rpc("evidence");
  const markers = [...Object.values(edited).flatMap(item => Array.isArray(item.value) ? item.value : [item.value]),
    "更新用のダミー回答文字列", "再更新用のダミー回答文字列", "確認後のダミー回答変更文字列", ...privateMarkers];
  const serialized = JSON.stringify(evidence);
  for (const marker of markers) assert.ok(!serialized.includes(marker), "Plaintext or key leaked to the server");
  assert.equal(evidence.counts.private_answer_documents, 5);
  assert.equal(evidence.counts.family_share_versions, 4);
  assert.equal(evidence.counts.qr_share_versions, 3);
  process.stdout.write(JSON.stringify({ kind: "done", counts: evidence.counts }) + "\n");
} catch (error) {
  process.stdout.write(JSON.stringify({ kind: "failed", stage, error: error.code || error.name }) + "\n");
  process.exitCode = 1;
} finally { input.close(); }
