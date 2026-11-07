import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, writeFile, mkdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { deliveryRequest, deliver, reconcileDeliveries, previewDelivery, makeDeliveryIO } from './delivery.mjs';

const HEAD = 'b'.repeat(40), BASE = 'a'.repeat(40);
async function fixture(t) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'light-delivery-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const config = { repository: 'owner/coach', state_dir: dir, workspace_id: 'workspace', project_id: 'project',
    delivery: { enabled: true, merge_method: 'squash' } };
  const request = { pr_number: 51, head_sha: HEAD, issue_id: '11111111-1111-4111-8111-111111111111',
    acceptance: { outcome: 'review_passed', summary: '四份文档验收通过；52个链接有效。', reference: 'DEMO-243 original review',
      url: 'https://example.com/review', checks: [{ command: 'git diff --check', status: 'PASS', exit_code: 0 }] } };
  const pr = { number: 51, state: 'open', draft: false, merged: false, auto_merge: null,
    base: { sha: BASE, repo: { full_name: 'owner/coach' } }, head: { sha: HEAD } };
  const issue = { id: request.issue_id, workspace_id: 'workspace', project_id: 'project', status: 'in_review' };
  const calls = { publish: 0, merge: 0, close: 0, live: 0 };
  let status = null;
  const io = {
    live: async () => { calls.live++; return structuredClone(pr); }, sharedHead: async () => [], status: async () => status,
    publish: async r => { calls.publish++; status = { state: 'success', target_url: r.acceptance.url,
      description: r.acceptance.outcome === 'owner_accepted' ? '维护者接受当前候选；原发现保留' : '复用已有验收；仅覆盖所引用的验收范围' }; },
    merge: async () => { calls.merge++; pr.state = 'closed'; pr.merged = true; },
    issue: async () => structuredClone(issue), close: async () => { calls.close++; issue.status = 'done'; },
  };
  const ledger = () => readFile(path.join(dir, 'delivery', 'pr-51.json'), 'utf8').then(JSON.parse);
  return { config, request, pr, issue, io, calls, dir, ledger };
}

test('existing document acceptance merges and closes without admission, five gates or new tasks', async t => {
  const f = await fixture(t);
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'DONE');
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [1, 1, 1]);
  assert.equal((await f.ledger()).request.acceptance.reference, f.request.acceptance.reference);
  await deliver(f.config, f.request, f.io);
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [1, 1, 1]);
});

test('head drift before publication produces zero external writes', async t => {
  const f = await fixture(t); f.pr.head.sha = 'c'.repeat(40);
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'CANDIDATE_CHANGED');
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [0, 0, 0]);
});

test('head drift after publication does not merge or close the changed candidate', async t => {
  const f = await fixture(t), publish = f.io.publish;
  f.io.publish = async r => { await publish(r); f.pr.head.sha = 'c'.repeat(40); };
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'CANDIDATE_CHANGED');
  assert.equal(f.calls.merge, 0); assert.equal(f.calls.close, 0);
});

test('explicit reviewed base drift waits for new acceptance', async t => {
  const f = await fixture(t); f.request.base_sha = 'c'.repeat(40);
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'BASE_CHANGED');
  assert.equal(f.calls.publish, 0);
});

test('maintainer acceptance preserves failed checks and findings while allowing delivery', async t => {
  const f = await fixture(t);
  f.request.acceptance.outcome = 'owner_accepted';
  f.request.acceptance.findings = ['Known issue accepted by Roland'];
  f.request.acceptance.checks[0] = { command: 'related test', status: 'FAIL', exit_code: 1 };
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'DONE');
  const record = (await f.ledger()).request.acceptance;
  assert.equal(record.outcome, 'owner_accepted'); assert.equal(record.checks[0].status, 'FAIL');
  assert.deepEqual(record.findings, ['Known issue accepted by Roland']);
});

test('failed or unrun checks cannot be described as review passed', async t => {
  const f = await fixture(t); f.request.acceptance.checks[0].status = 'FAIL'; f.request.acceptance.checks[0].exit_code = 1;
  await assert.rejects(deliver(f.config, f.request, f.io), /explicit owner acceptance/);
  assert.equal(f.calls.publish, 0);
});

for (const step of ['publish', 'merge', 'close']) test(`lost ${step} response reconciles without a second write`, async t => {
  const f = await fixture(t), action = f.io[step];
  f.io[step] = async arg => { await action(arg); throw new Error('response lost'); };
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'DONE');
  await reconcileDeliveries(f.config, f.io);
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [1, 1, 1]);
});

test('pending GitHub auto-merge is polled without another merge request', async t => {
  const f = await fixture(t);
  f.io.merge = async () => { f.calls.merge++; f.pr.auto_merge = { merge_method: 'squash' }; };
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'WAITING_MERGE');
  assert.equal((await reconcileDeliveries(f.config, f.io)).tasks[0].status, 'WAITING_MERGE');
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [1, 1, 0]);
  f.pr.state = 'closed'; f.pr.merged = true;
  assert.equal((await reconcileDeliveries(f.config, f.io)).tasks[0].status, 'DONE');
  assert.equal(f.calls.close, 1);
});

test('publication failure is retried by the program without restarting review', async t => {
  const f = await fixture(t), publish = f.io.publish;
  f.io.publish = async () => { f.calls.publish++; throw new Error('offline'); };
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'RETRY_IO');
  f.io.publish = publish;
  assert.equal((await reconcileDeliveries(f.config, f.io)).tasks[0].status, 'DONE');
  assert.equal(f.calls.merge, 1);
});

test('unrelated green status does not impersonate this acceptance', async t => {
  const f = await fixture(t);
  f.io.status = async () => ({ state: 'success', target_url: 'https://example.com/other', description: 'old success' });
  f.io.publish = async () => { f.calls.publish++; throw new Error('not sent'); };
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'RETRY_IO');
  assert.equal(f.calls.merge, 0);
});

test('simultaneous same-task submissions execute a single writer', async t => {
  const f = await fixture(t);
  const results = await Promise.all([deliver(f.config, f.request, f.io), deliver(f.config, f.request, f.io)]);
  assert(results.some(r => r.status === 'DONE')); assert(results.some(r => r.status === 'ALREADY_RUNNING'));
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [1, 1, 1]);
});

test('different tasks do not share a global lock', async t => {
  const f = await fixture(t); let release;
  const blocked = new Promise(resolve => { release = resolve; });
  const firstLive = f.io.live;
  f.io.live = async () => { await blocked; return firstLive(); };
  const first = deliver(f.config, f.request, f.io);
  const r = { ...f.request, pr_number: 52, issue_id: null }, p = { ...f.pr, number: 52, state: 'closed', merged: true };
  const second = await deliver(f.config, r, { ...f.io, live: async () => p });
  assert.equal(second.status, 'DONE'); release(); await first;
});

test('shared SHA across open PRs prevents misattributed commit status', async t => {
  const f = await fixture(t); f.io.sharedHead = async () => [52];
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'SHARED_HEAD');
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [0, 0, 0]);
});

test('closed unmerged PR never closes the business issue', async t => {
  const f = await fixture(t); f.pr.state = 'closed';
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'CLOSED_UNMERGED');
  assert.equal(f.calls.close, 0);
});

test('reopened delivered PR needs a new acceptance before writing again', async t => {
  const f = await fixture(t); await deliver(f.config, f.request, f.io);
  f.pr.state = 'open'; f.pr.merged = false;
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'REOPENED');
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'REOPENED');
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [1, 1, 1]);
  f.request.acceptance.reference = 'fresh acceptance after reopen';
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'DONE');
});

test('issue project mismatch stops before publishing', async t => {
  const f = await fixture(t); f.issue.project_id = 'other';
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'BLOCKED');
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [0, 0, 0]);
});

test('new head keeps previous acceptance history', async t => {
  const f = await fixture(t); await deliver(f.config, f.request, f.io);
  f.pr.state = 'open'; f.pr.merged = false; f.pr.head.sha = 'c'.repeat(40); f.request.head_sha = f.pr.head.sha;
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'DONE');
  assert.equal((await f.ledger()).history[0].request.head_sha, HEAD);
});

test('idle reconciliation needs no platform calls or maintenance capabilities', async t => {
  const f = await fixture(t);
  assert.deepEqual(await reconcileDeliveries(f.config, {}), { status: 'OK', tasks: [] });
  assert.equal(f.calls.live, 0);
});

test('preview never writes even for an already merged candidate', async t => {
  const f = await fixture(t); f.pr.state = 'closed'; f.pr.merged = true;
  assert.equal((await previewDelivery(f.config, f.request, f.io)).status, 'MERGED');
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [0, 0, 0]);
});

test('CLI adapters use head matching and no-start, and never create tasks or mention agents', async t => {
  const f = await fixture(t), calls = [];
  const io = makeDeliveryIO(f.config, async (file, args) => { calls.push(args); return '{}'; });
  await io.merge(deliveryRequest(f.config, f.request)); await io.close(f.request.issue_id);
  assert(calls[0].includes('--match-head-commit')); assert(calls[0].includes(HEAD)); assert(calls[0].includes('--auto'));
  assert(calls[1].includes('--no-start')); assert(!calls.flat().includes('create')); assert(!calls.flat().includes('--admin'));
});

test('source extension fields and reordered keys do not invalidate an accepted request', async t => {
  const f = await fixture(t); await deliver(f.config, f.request, f.io);
  f.request.acceptance = { reference: f.request.acceptance.reference, url: f.request.acceptance.url,
    checks: f.request.acceptance.checks, summary: f.request.acceptance.summary, outcome: 'review_passed' };
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'DONE');
});

test('configured repository cannot change underneath a queued request', async t => {
  const f = await fixture(t); await deliver(f.config, f.request, f.io);
  f.config.repository = 'owner/other';
  await assert.rejects(deliver(f.config, (await f.ledger()).request, f.io), /request repository differs/);
});

test('repeated transport failures stop at the configured retry budget and explicit submit can resume', async t => {
  const f = await fixture(t), publish = f.io.publish;
  f.config.delivery.max_retry_attempts = 2;
  f.io.publish = async () => { throw new Error('offline'); };
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'RETRY_IO');
  assert.equal((await reconcileDeliveries(f.config, f.io)).tasks[0].status, 'NEEDS_ATTENTION');
  assert.deepEqual((await reconcileDeliveries(f.config, f.io)).tasks, []);
  f.io.publish = publish;
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'DONE');
});

test('one corrupted task cannot block unrelated queued work', async t => {
  const f = await fixture(t); await mkdir(path.join(f.dir, 'delivery'));
  await writeFile(path.join(f.dir, 'delivery', 'pr-50.json'), '{bad json');
  await writeFile(path.join(f.dir, 'delivery', 'pr-51.json'), JSON.stringify({ request: deliveryRequest(f.config, f.request), status: 'NEW' }));
  const result = await reconcileDeliveries(f.config, f.io);
  assert(result.tasks.some(task => task.status === 'BLOCKED')); assert(result.tasks.some(task => task.status === 'DONE'));
});

test('an incomplete lock from a concurrently starting writer is retained', async t => {
  const f = await fixture(t); await mkdir(path.join(f.dir, 'delivery'));
  await writeFile(path.join(f.dir, 'delivery', 'pr-51.lock'), '');
  assert.equal((await deliver(f.config, f.request, f.io)).status, 'ALREADY_RUNNING');
  assert.equal(f.calls.publish, 0);
});
