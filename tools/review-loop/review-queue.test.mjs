import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, writeFile, mkdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { validateReview, registerReview, discoverReviews, runQueuedReview, reconcileReviews, runReviewCli, registerGitReview, reviewContext, makeReviewIO, retryReview } from './review-queue.mjs';

const HEAD = 'a'.repeat(40), NEXT = 'b'.repeat(40), BASE = 'c'.repeat(40);
const pass = () => ({ verdict: 'PASS', summary: '本票接受条件通过', findings: [],
  checks: [{ command: 'related regression', status: 'PASS', exit_code: 0 }], limitations: [] });
const changes = () => ({ ...pass(), verdict: 'CHANGES_REQUIRED', findings: [{ id: 'R1', severity: 'P2', path: 'src/case.ts',
  line: 10, problem: 'invalid selection changes denominator', evidence: 'invalid-case regression', fix: 'include invalid cases' }] });
async function fixture(t) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'codex-review-queue-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const config = { repository: 'owner/example', state_dir: dir, workspace_id: 'workspace', project_id: 'product', project_ids: ['addon'],
    review: { enabled: true, engine: 'multica', max_rounds: 2 }, delivery: { enabled: true, merge_method: 'squash' } };
  const issue = { id: '11111111-1111-4111-8111-111111111111', workspace_id: 'workspace', project_id: 'addon', status: 'in_review' };
  const pr = { number: 53, state: 'open', draft: false, merged: false, auto_merge: null, title: 'EXAMPLE-220 Arena', body: 'scope',
    head: { sha: HEAD, ref: 'author/arena' }, base: { sha: BASE, repo: { full_name: config.repository } } };
  const calls = { review: 0, fix: 0, push: 0, report: 0, publish: 0, merge: 0, close: 0 };
  let status = null;
  const io = { live: async () => structuredClone(pr), issueByKey: async () => structuredClone(issue), issue: async () => structuredClone(issue),
    runs: async () => [], openPRs: async () => [structuredClone(pr)], prepare: async () => '/test/worktree', git: async () => '',
    codex: async (_job, kind) => { calls[kind]++; return pass(); },
    pushFix: async () => { calls.push++; pr.head.sha = NEXT; return NEXT; },
    report: async () => { calls.report++; return 'https://github.com/owner/example/pull/53#issuecomment-1'; },
    sharedHead: async () => [], status: async () => status,
    publish: async r => { calls.publish++; status = { state: 'success', target_url: r.acceptance.url,
      description: '复用已有验收；仅覆盖所引用的验收范围' }; },
    merge: async () => { calls.merge++; pr.state = 'closed'; pr.merged = true; },
    close: async () => { calls.close++; issue.status = 'done'; } };
  const file = path.join(dir, 'review-queue/pr-53.json');
  const ledger = () => readFile(file, 'utf8').then(JSON.parse);
  const register = () => registerReview(config, 53, issue.id, io);
  return { dir, config, issue, pr, io, calls, file, ledger, register };
}

test('PASS cannot hide blockers, failures, or absence of independent checks', () => {
  assert.throws(() => validateReview({ ...changes(), verdict: 'PASS' }), /contradicts/);
  assert.throws(() => validateReview({ ...pass(), checks: [] }), /contradicts/);
  assert.throws(() => validateReview({ ...pass(), checks: [{ command: 'test', status: 'FAIL', exit_code: 1 }] }), /contradicts/);
});

async function nativeFixture(t) {
  const f = await fixture(t); f.config.review.engine = 'multica'; f.config.reviewer_id = 'reviewer'; f.config.fixer_id = 'coder'; f.config.runtime_root = f.dir;
  const history = []; f.io.runs = async () => structuredClone(history);
  f.io.assign = async (issue, agent) => { assert.equal(issue, f.issue.id); f.issue.assignee_id = agent; f.calls.assign = (f.calls.assign ?? 0) + 1; };
  f.io.rerun = async () => { f.calls.rerun = (f.calls.rerun ?? 0) + 1; history.push({ id: `run-${history.length}`, agent_id: f.issue.assignee_id, status: 'running' }); };
  f.io.git = async args => args[0] === 'rev-parse' ? f.pr.head.sha : '';
  f.io.statusReview = async () => { f.issue.status = 'in_review'; };
  f.io.codex = async () => { throw new Error('Codex must remain standby'); };
  const result = async (review = pass(), head = f.pr.head.sha) => {
    const job = await f.ledger(); await writeFile(job.result_path, JSON.stringify({ head_sha: head, ...review })); return job;
  };
  return { ...f, history, result };
}
test('GitHub candidate discovery uses configured issue prefix and the mapped workspace', async t => {
  const f = await nativeFixture(t);
  f.config.review.issue_prefixes = ['PORT'];
  f.pr.title = 'PORT-220 changes'; f.pr.body = 'OLD-25 is unrelated';
  f.io.issueByKey = async key => {
    assert(['PORT-220', f.issue.id].includes(key));
    return structuredClone(f.issue);
  };
  assert.equal((await discoverReviews(f.config, f.io))[0].status, 'QUEUED');
  f.issue.project_id = 'unmapped';
  assert.equal((await discoverReviews(f.config, f.io))[0].status, 'SKIPPED');
});

test('GitHub candidate source can run the same review without granting delivery writes', async t => {
  const f = await nativeFixture(t);
  f.config.delivery.enabled = false;
  await f.register();
  await runQueuedReview(f.config, 53, f.io);
  await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'PASSED');
  assert.deepEqual([f.calls.report, f.calls.publish, f.calls.merge, f.calls.close], [0, 0, 0, 0]);
});

test('default native route hands the existing issue directly to Reviewer and never calls Codex', async t => {
  const f = await nativeFixture(t); await f.register();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NATIVE_REVIEWING');
  assert.equal(f.issue.assignee_id, 'reviewer'); assert.equal(f.calls.rerun, 1);
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW'); assert.equal(f.calls.rerun, 1);
  const context = await reviewContext(f.config, f.issue.id, f.io);
  assert.equal(Object.hasOwn(context, 'complete_command'), false);
  assert.match(context.result_submission, /intermediary owns/);
  const job = await f.result();
  assert.deepEqual([f.calls.report, f.calls.publish, f.calls.merge, f.calls.close], [0, 0, 0, 0]);
  assert.equal((await f.ledger()).status, 'NATIVE_REVIEWING');
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [1, 1, 1]);
  await runQueuedReview(f.config, 53, f.io); assert.equal(f.calls.merge, 1);
});
test('intermediary consumes the submitted result without any Reviewer callback', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io); await f.result();
  assert.equal((await reconcileReviews(f.config, f.io)).tasks[0].status, 'DONE'); assert.equal(f.calls.rerun, 1);
});
test('a Windows UTF-8 BOM does not turn valid Reviewer evidence into a formatting blocker', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.result(); const content = await readFile(job.result_path, 'utf8');
  await writeFile(job.result_path, '\uFEFF' + content);
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
});
test('native blocking result returns to the same coding issue, retains the round budget, then re-reviews', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.result(changes());
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'REPAIR_READY');
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  f.history[0].status = 'completed';
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'AWAITING_FIX'); assert.equal(f.issue.assignee_id, 'coder');
  assert.equal((await reviewContext(f.config, f.issue.id, f.io)).stage, 'fix');
  f.pr.head.sha = NEXT; await f.register(); assert.equal((await f.ledger()).round, 1);
  f.history[1].status = 'completed'; await runQueuedReview(f.config, 53, f.io);
  assert.equal(f.issue.assignee_id, 'reviewer'); assert.equal((await f.ledger()).round, 2);
  const second = await f.result(); assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  assert.equal(f.calls.rerun, 3);
});
test('native second and later P1/P2 results continue repair despite obsolete configured cap', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.result(changes()); job.round = 2; await writeFile(f.file, JSON.stringify(job));
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'REPAIR_READY');
  f.history[0].status = 'completed';
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'AWAITING_FIX');
  f.pr.head.sha = NEXT; await f.register(); f.history[1].status = 'completed';
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NATIVE_REVIEWING');
  assert.equal((await f.ledger()).round, 3); await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  assert.equal(f.calls.rerun, 3); assert.equal(f.calls.publish, 1);
});

const disconnect = () => Object.assign(new Error('Multica connection lost'), { transport: true });

test('dispatch recovery reuses the current run snapshot without inventing a review round', async t => {
  const f = await nativeFixture(t); await f.register();
  const runs = f.io.runs; let reads = 0;
  f.io.runs = async () => { reads++; if (reads !== 2) throw disconnect(); return runs(); };
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'RETRY_IO');
  assert.equal((await f.ledger()).round, 0);
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NATIVE_REVIEWING');
  assert.equal(reads, 2); assert.equal((await f.ledger()).round, 1); assert.equal(f.calls.rerun, 1);
});

test('a read disconnect preserves native stage and consumes a later result without rerunning', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const issue = f.io.issue; f.io.issue = async () => { throw disconnect(); };
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'RETRY_IO');
  assert.equal((await f.ledger()).status, 'NATIVE_REVIEWING');
  await f.result(); f.io.issue = issue;
  assert.equal((await reconcileReviews(f.config, f.io)).tasks[0].status, 'DONE');
  assert.equal(f.calls.rerun, 1); assert.equal(f.calls.close, 1);
});

test('old attention record caused by a command disconnect recovers the submitted second review', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.result(changes()); job.status = 'NEEDS_ATTENTION'; job.round = 2;
  job.error = 'multica.exe operation failed (--profile); inspect locally'; await writeFile(f.file, JSON.stringify(job));
  assert.equal((await reconcileReviews(f.config, f.io)).tasks[0].status, 'REPAIR_READY');
  assert.equal((await f.ledger()).last_review.verdict, 'CHANGES_REQUIRED');
  assert.equal(f.calls.rerun, 1); assert.equal(f.calls.report, 1);
});

test('downstream disconnect resumes a saved review without posting a second report', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io); await f.result(changes());
  const status = f.io.statusReview; f.io.statusReview = async () => { throw disconnect(); };
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'RETRY_IO'); assert.equal(f.calls.report, 1);
  f.io.statusReview = status;
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'REPAIR_READY'); assert.equal(f.calls.report, 1);
});

test('assignment disconnect resumes the persisted dispatch before sending one run', async t => {
  const f = await nativeFixture(t), assign = f.io.assign; await f.register();
  f.io.assign = async () => { throw disconnect(); };
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'RETRY_IO');
  assert.equal((await f.ledger()).status, 'SENDING'); assert.equal(f.calls.rerun ?? 0, 0);
  f.io.assign = assign;
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NATIVE_REVIEWING'); assert.equal(f.calls.rerun, 1);
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW'); assert.equal(f.calls.rerun, 1);
});

test('a repair assignment disconnect never consumes the previous review as a new review', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io); await f.result(changes());
  await runQueuedReview(f.config, 53, f.io); f.history[0].status = 'completed';
  const assign = f.io.assign; f.io.assign = async () => { throw disconnect(); };
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'RETRY_IO');
  f.io.assign = assign;
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'AWAITING_FIX');
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_AUTHOR');
  assert.equal(f.calls.report, 1); assert.equal(f.calls.rerun, 2);
});

test('an old round-limit stop resumes repair but a real BLOCKED result remains stopped', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.result(changes()); job.round = 2; job.last_review = changes();
  job.reviews = [{ head_sha: HEAD, result: changes(), result_path: job.result_path }]; job.status = 'NEEDS_ATTENTION';
  await writeFile(f.file, JSON.stringify(job)); f.history[0].status = 'completed';
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'AWAITING_FIX');
  job.last_review = { ...pass(), verdict: 'BLOCKED', checks: [] }; job.status = 'NEEDS_ATTENTION';
  await writeFile(f.file, JSON.stringify(job));
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NEEDS_ATTENTION'); assert.equal(f.calls.rerun, 2);
});

test('GitHub report response loss reuses the actual comment instead of posting twice', async t => {
  const f = await fixture(t), comments = []; let writes = 0;
  const io = makeReviewIO({ repository: 'owner/example', gh_path: 'gh' }, async (_file, args) => {
    if (args[0] === 'api') return JSON.stringify([comments]);
    assert.deepEqual(args.slice(0, 2), ['pr', 'comment']); writes++;
    comments.push({ body: await readFile(args[args.indexOf('--body-file') + 1], 'utf8'), html_url: 'https://github.com/owner/example/pull/53#issuecomment-1' });
    throw disconnect();
  });
  const job = { engine: 'multica', pr_number: 53, head_sha: HEAD, last_review: pass() }, file = path.join(f.dir, 'review.json');
  await assert.rejects(io.report(job, file), /connection lost/);
  assert.equal(await io.report(job, file), comments[0].html_url); assert.equal(writes, 1);
});

test('failed open-PR discovery cannot block consumption of a known saved result', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io); await f.result();
  f.io.openPRs = async () => { throw disconnect(); };
  const result = await reconcileReviews(f.config, f.io);
  assert.equal(result.discovered[0].status, 'RETRY_IO'); assert.equal(result.tasks[0].status, 'DONE');
});
test('native stale candidate or result cannot turn the PR green', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.result(pass(), NEXT);
  const result = await runQueuedReview(f.config, 53, f.io);
  assert.equal(result.status, 'NEEDS_ATTENTION'); assert.match(result.reason, /another candidate/);
  assert.equal(f.calls.publish, 0); assert.equal(f.calls.merge, 0);
});
test('native lost rerun response reconciles the first run without dispatching a second', async t => {
  const f = await nativeFixture(t), rerun = f.io.rerun;
  f.io.rerun = async () => { await rerun(); throw new Error('response lost'); };
  await f.register(); await runQueuedReview(f.config, 53, f.io); await runQueuedReview(f.config, 53, f.io);
  assert.equal(f.calls.rerun, 1); const job = await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
});
test('native blocked review is reported without silently falling back to Codex', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.result({ ...pass(), verdict: 'BLOCKED', checks: [], limitations: ['environment unavailable'] });
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NEEDS_ATTENTION');
  assert.equal(f.calls.report, 1); assert.equal(f.calls.publish, 0);
});
test('native completed run without a result reports a concrete stop', async t => {
  const f = await nativeFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io); f.history[0].status = 'completed';
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NEEDS_ATTENTION'); assert.equal(f.calls.rerun, 1);
});
test('native CLI adapter reuses issue ownership and fresh run without creating any issue or Controller mention', async () => {
  const calls = [], config = { multica_path: 'multica', workspace_id: 'w', profile: 'p' };
  const io = makeReviewIO(config, async (file, args) => { calls.push({ file, args }); return '{}'; });
  await io.assign('business-issue', 'reviewer'); await io.rerun('business-issue');
  assert(calls[0].args.includes('--no-start')); assert(calls[1].args.includes('rerun'));
  assert(calls.every(call => !call.args.includes('create') && !call.args.includes('comment')));
});
test('a waiting native review does not hold unrelated business PRs behind it', async t => {
  const f = await nativeFixture(t), secondIssue = '22222222-2222-4222-8222-222222222222';
  f.io.runs = async id => f.history.filter(run => run.issue_id === id);
  f.io.rerun = async id => { f.calls.rerun = (f.calls.rerun ?? 0) + 1; f.history.push({ id: `r-${f.history.length}`, issue_id: id, agent_id: 'reviewer', status: 'running' }); };
  f.io.assign = async () => {};
  f.io.live = async number => ({ ...structuredClone(f.pr), number });
  f.io.issueByKey = f.io.issue = async id => ({ ...f.issue, id });
  f.io.openPRs = async () => [];
  await f.register(); await runQueuedReview(f.config, 53, f.io);
  await registerReview(f.config, 54, secondIssue, f.io);
  const result = await reconcileReviews(f.config, f.io);
  assert(result.tasks.some(task => task.pr_number === 53 && task.status === 'WAITING_REVIEW'));
  assert(result.tasks.some(task => task.pr_number === 54 && task.status === 'NATIVE_REVIEWING'));
  assert.equal(f.calls.rerun, 2);
});

async function pairedNativeQueue(t, engine) {
  const f = engine === 'multi' ? await priorityFixture(t) : await nativeFixture(t);
  const secondIssue = '22222222-2222-4222-8222-222222222222';
  const assignees = new Map();
  f.io.runs = async id => f.history.filter(run => run.issue_id === id);
  f.io.assign = async (id, agent) => { assignees.set(id, agent); };
  f.io.rerun = async id => {
    f.calls.rerun = (f.calls.rerun ?? 0) + 1;
    f.history.push({ id: `paired-${f.history.length}`, issue_id: id, agent_id: assignees.get(id), status: 'running' });
  };
  f.io.live = async number => ({ ...structuredClone(f.pr), number });
  f.io.issueByKey = f.io.issue = async id => ({ ...f.issue, id });
  f.io.openPRs = async () => [];
  await f.register(); await registerReview(f.config, 54, secondIssue, f.io);
  const second = () => readFile(path.join(f.dir, 'review-queue/pr-54.json'), 'utf8').then(JSON.parse);
  return { ...f, secondIssue, second };
}

for (const engine of ['multica', 'multi']) {
  test(`${engine}: reconciliation dispatches a later native job after a new review dispatch`, async t => {
    const f = await pairedNativeQueue(t, engine);
    const result = await reconcileReviews(f.config, f.io);
    assert(result.tasks.some(task => task.pr_number === 53));
    assert(result.tasks.some(task => task.pr_number === 54), 'a native review transition cannot end the queue pass');
    assert.equal((await f.second()).status, 'NATIVE_REVIEWING');
    assert.equal(f.calls.rerun, 2);
    if (engine === 'multi') assert.equal((await f.second()).native.agent_id, 'reviewer-b', 'the occupied reviewer still counts');
  });

  test(`${engine}: reconciliation recovers a review dispatch and still reaches the later job`, async t => {
    const f = await pairedNativeQueue(t, engine);
    await runQueuedReview(f.config, 53, f.io);
    const pending = await f.ledger(); pending.status = 'SENDING';
    await writeFile(f.file, JSON.stringify(pending));
    const result = await reconcileReviews(f.config, f.io);
    assert(result.tasks.some(task => task.pr_number === 53 && task.status === 'NATIVE_REVIEWING'));
    assert(result.tasks.some(task => task.pr_number === 54), 'reusing the first run cannot stop later jobs');
    assert.equal(f.history.filter(run => run.issue_id === f.issue.id).length, 1, 'dispatch recovery must not duplicate the first run');
    assert.equal((await f.second()).status, 'NATIVE_REVIEWING');
    assert.equal(f.calls.rerun, 2);
  });

  test(`${engine}: reconciliation hands a ready repair to its fixer and still dispatches the later review`, async t => {
    const f = await pairedNativeQueue(t, engine);
    await runQueuedReview(f.config, 53, f.io); await f.result(changes());
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'REPAIR_READY');
    f.history[0].status = 'completed';
    const result = await reconcileReviews(f.config, f.io);
    assert(result.tasks.some(task => task.pr_number === 53 && task.status === 'AWAITING_FIX'));
    assert(result.tasks.some(task => task.pr_number === 54), 'a fixer dispatch cannot stop an unrelated review');
    assert.equal((await f.ledger()).native.stage, 'fix');
    assert.equal((await f.second()).status, 'NATIVE_REVIEWING');
    assert.equal(f.calls.rerun, 3);
  });

  test(`${engine}: an already running fixer remains intact while a later review is dispatched`, async t => {
    const f = await pairedNativeQueue(t, engine);
    await runQueuedReview(f.config, 53, f.io); await f.result(changes());
    await runQueuedReview(f.config, 53, f.io); f.history[0].status = 'completed';
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'AWAITING_FIX');
    const before = await f.ledger();
    const result = await reconcileReviews(f.config, f.io);
    assert(result.tasks.some(task => task.pr_number === 53 && task.status === 'WAITING_AUTHOR'));
    assert(result.tasks.some(task => task.pr_number === 54));
    assert.deepEqual(await f.ledger(), before, 'the active fixer ledger must not be reset');
    assert.equal(f.history.filter(run => run.issue_id === f.issue.id && run.agent_id === 'coder').length, 1);
    assert.equal((await f.second()).status, 'NATIVE_REVIEWING');
  });
}

test('continuing reconciliation still reserves a shared browser slot for a recovered native run', async t => {
  const f = await pairedNativeQueue(t, 'multi');
  for (const reviewer of f.config.review.reviewers) reviewer.capacity_key = 'browser';
  f.config.review.capacity_resources = { browser: { max_concurrency: 1, status_url: 'http://bridge/status' } };
  f.io.capacity = async () => ({ capacity: 1, busy: false });
  await f.register(); await registerReview(f.config, 54, f.secondIssue, f.io);
  await runQueuedReview(f.config, 53, f.io);
  const first = await f.ledger(); first.status = 'SENDING'; await writeFile(f.file, JSON.stringify(first));
  const result = await reconcileReviews(f.config, f.io);
  assert(result.tasks.some(task => task.pr_number === 54 && task.status === 'WAITING_CAPACITY'));
  assert.equal((await f.second()).status, 'WAITING_CAPACITY');
  assert.equal(f.calls.rerun, 1, 'continuing the scan must not create physical browser capacity');
});

test('concurrent reconciliation passes still share the existing queue lock', async t => {
  const f = await pairedNativeQueue(t, 'multica');
  const results = await Promise.all([reconcileReviews(f.config, f.io), reconcileReviews(f.config, f.io)]);
  assert.deepEqual(results.map(result => result.status).sort(), ['ALREADY_RUNNING', 'OK']);
  assert.equal(f.history.filter(run => run.issue_id === f.issue.id).length, 1);
});

test('removed complete entry rejects before reading configuration or contacting platforms', async () => {
  await assert.rejects(runReviewCli(['complete', 'missing-config.json', '53']), /complete has been removed/);
});

// --- multi-reviewer engine ---------------------------------------------------
async function multiFixture(t, { mode = 'all' } = {}) {
  const f = await nativeFixture(t);
  f.config.review.engine = 'multi';
  f.config.review.mode = mode;
  f.config.review.reviewers = [
    { key: 'primary', label: 'Primary', agent_id: 'reviewer-a' },
    { key: 'second', label: 'Second', agent_id: 'reviewer-b' },
  ];
  return f;
}

test('multi: dispatches every required reviewer at one head and delivers the combined PASS once', async t => {
  const f = await multiFixture(t); await f.register();
  assert.equal(f.calls.review ?? 0, 0);
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-a');
  let job = await f.ledger();
  assert.equal(job.engine, 'multi');
  assert.equal(job.reviewers.length, 2);
  assert.match(job.result_path, /primary-review\.json$/);

  await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-b');
  job = await f.ledger();
  assert.match(job.result_path, /second-review\.json$/);

  await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  job = await f.ledger();
  assert.equal(job.last_review.verdict, 'PASS');
  assert.equal(job.delivery_request.acceptance.reviewers.length, 2);
  assert.deepEqual(job.delivery_request.acceptance.reviewers.map(r => r.key), ['primary', 'second']);
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [1, 1, 1]);
  assert.equal(f.calls.rerun, 2);
});

test('multi: one blocking reviewer still lets the others review, and the combined verdict repairs', async t => {
  const f = await multiFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  await f.result(changes());
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-b', 'a blocking result must not skip the remaining reviewers');
  await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'REPAIR_READY');
  const job = await f.ledger();
  assert.equal(job.last_review.verdict, 'CHANGES_REQUIRED');
  assert.deepEqual(job.last_review.findings.map(item => item.reviewer), ['primary']);
  assert.deepEqual([f.calls.publish, f.calls.merge, f.calls.close], [0, 0, 0]);
});

test('multi: switching the reviewer set re-queues the same head instead of reusing the old panel', async t => {
  const f = await multiFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  await f.result({ ...pass(), verdict: 'BLOCKED', checks: [], limitations: ['harness refused the exact command'] });
  await runQueuedReview(f.config, 53, f.io);
  await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NEEDS_ATTENTION');
  const before = await f.ledger();
  assert.equal(before.reviewers.length, 2);
  assert.equal(before.last_review.verdict, 'BLOCKED');

  f.config.review.reviewers = [{ key: 'second', label: 'Second', agent_id: 'reviewer-b' }];
  const registered = await f.register();
  assert.equal(registered.status, 'QUEUED');
  assert.equal(registered.reset, 'reviewer-set');
  const after = await f.ledger();
  assert.deepEqual(after.reviewers.map(item => item.key), ['second']);
  assert.deepEqual(after.review_results, []);
  assert.equal(after.last_review, undefined);
  assert.ok((after.history ?? []).some(item => item.event === 'reviewer_set_changed'));
});

test('multi first_pass: a passing reviewer ends the round without dispatching the rest', async t => {
  const f = await multiFixture(t, { mode: 'first_pass' }); await f.register(); await runQueuedReview(f.config, 53, f.io);
  await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  const job = await f.ledger();
  assert.equal(job.last_review.verdict, 'PASS');
  assert.equal(job.review_results.length, 1);
  assert.match(job.last_review.limitations.join('\n'), /\[second\] skipped/);
  assert.equal(f.calls.rerun, 1);
});

test('multi: reviewer context names the current reviewer and its own result path', async t => {
  const f = await multiFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const context = await reviewContext(f.config, f.issue.id, f.io);
  assert.equal(context.reviewer.key, 'primary');
  assert.equal(context.review_mode, 'all');
  assert.deepEqual(context.reviewers.map(item => item.key), ['primary', 'second']);
  assert.match(context.result_path, /primary-review\.json$/);
});
// --- priority mode -----------------------------------------------------------
async function priorityFixture(t, reviewers = null) {
  const f = await nativeFixture(t);
  f.config.review.engine = 'multi';
  f.config.review.mode = 'priority';
  f.config.review.reviewers = reviewers ?? [
    { key: 'gptweb', label: 'GPT-Web', agent_id: 'reviewer-a' },
    { key: 'local', label: 'Local', agent_id: 'reviewer-b' },
  ];
  return f;
}

const sibling = (pr, key, agent) => ({
  workspace_id: 'workspace',
  repository: 'owner/example', pr_number: pr, issue_id: `issue-${pr}`, head_sha: 'd'.repeat(40),
  engine: 'multi', status: 'NATIVE_REVIEWING', reviewers: [{ key, required: true }], review_mode: 'priority',
  native: { stage: 'review', reviewer_key: key, agent_id: agent, prior_run_ids: [], requested_at: new Date().toISOString() },
});

test('priority: the highest-priority reviewer decides the round alone', async t => {
  const f = await priorityFixture(t); await f.register();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-a');
  let job = await f.ledger();
  assert.match(job.result_path, /gptweb-review\.json$/);
  await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  job = await f.ledger();
  assert.equal(job.last_review.verdict, 'PASS');
  assert.deepEqual(job.delivery_request.acceptance.reviewers.map(item => item.key), ['gptweb']);
  assert.equal(f.calls.rerun, 1, 'the lower-priority reviewer is never dispatched');
});

test('priority: a saturated top reviewer hands the review to the next one', async t => {
  const f = await priorityFixture(t); await f.register();
  await writeFile(path.join(f.dir, 'review-queue', 'pr-54.json'), JSON.stringify(sibling(54, 'gptweb', 'reviewer-a')));
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-b');
  const job = await f.ledger();
  assert.match(job.result_path, /local-review\.json$/);
});

test('priority: with every reviewer saturated the round waits for capacity', async t => {
  const f = await priorityFixture(t); await f.register();
  await writeFile(path.join(f.dir, 'review-queue', 'pr-54.json'), JSON.stringify(sibling(54, 'gptweb', 'reviewer-a')));
  await writeFile(path.join(f.dir, 'review-queue', 'pr-55.json'), JSON.stringify(sibling(55, 'local', 'reviewer-b')));
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_CAPACITY');
  assert.equal((await f.ledger()).status, 'WAITING_CAPACITY');
  assert.equal(f.calls.rerun ?? 0, 0);
});

test('priority: a raised max_concurrency keeps the top reviewer eligible', async t => {
  const f = await priorityFixture(t, [
    { key: 'gptweb', label: 'GPT-Web', agent_id: 'reviewer-a', max_concurrency: 2 },
    { key: 'local', label: 'Local', agent_id: 'reviewer-b' },
  ]);
  await f.register();
  await writeFile(path.join(f.dir, 'review-queue', 'pr-54.json'), JSON.stringify(sibling(54, 'gptweb', 'reviewer-a')));
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-a');
});


test('retry re-queues a review that ended without a verdict', async t => {
  const f = await priorityFixture(t); await f.register();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  for (const run of f.history) if (run.agent_id === 'reviewer-a') run.status = 'completed';
  f.history.push({ id: 'run-no-result', agent_id: 'reviewer-a', status: 'completed' });
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NEEDS_ATTENTION');
  const before = await f.ledger();

  const retried = await retryReview(f.config, 53, f.io);
  assert.equal(retried.status, 'QUEUED');
  const after = await f.ledger();
  assert.equal(after.status, 'QUEUED');
  assert.equal(after.head_sha, before.head_sha);
  assert.deepEqual(after.reviewers.map(item => item.key), before.reviewers.map(item => item.key));
  assert.equal(after.native, undefined);
  assert.equal(after.retry_count, 1);
  assert.ok((after.history ?? []).some(item => item.event === 'retry'));

  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-a');
});

test('retry refuses a delivered job and a review that is still in flight', async t => {
  const f = await priorityFixture(t); await f.register();
  await runQueuedReview(f.config, 53, f.io);
  await assert.rejects(retryReview(f.config, 53, f.io), /NEEDS_ATTENTION/);
  await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  await assert.rejects(retryReview(f.config, 53, f.io), /NEEDS_ATTENTION/);
});

for (const engine of ['multica', 'multi']) {
  test(`${engine}: retry recovers a new-head preparation failure after the caller directory is corrected`, async t => {
    const f = engine === 'multi' ? await priorityFixture(t) : await nativeFixture(t);
    await f.register();
    const original = await f.ledger();
    original.status = 'AWAITING_FIX'; original.round = 1;
    const previous = { head_sha: HEAD, ...changes() };
    original.last_review = previous;
    original.reviews = [{ head_sha: HEAD, result: previous, result_path: '/previous/review.json' }];
    await writeFile(f.file, JSON.stringify(original));
    f.pr.head.sha = NEXT;
    const callerDirectory = path.join(f.dir, 'caller-old-review'); await mkdir(callerDirectory);
    await registerReview(f.config, 53, f.issue.id, f.io, callerDirectory);
    let checkout = HEAD;
    const strict = makeReviewIO(f.config, async (_file, args, cwd) => {
      assert.equal(cwd, callerDirectory, 'preparation must inspect the exact caller directory');
      if (args[1] === '--show-toplevel') return callerDirectory;
      if (args[0] === 'rev-parse') return checkout;
      if (args[0] === 'status') return '';
      throw new Error('preparation must not move or replace the caller directory');
    });
    f.io.prepare = strict.prepare;
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NEEDS_ATTENTION');
    const failed = await f.ledger();
    assert.match(failed.error, /review checkout head changed/);
    assert.equal(failed.native, undefined); assert.equal(failed.result_path, undefined);
    assert.equal(failed.worktree, callerDirectory); assert.equal(f.calls.rerun ?? 0, 0);
    checkout = NEXT; // The operator has safely corrected the directory outside retry.
    f.history.push({ id: 'ended-fixer', agent_id: 'coder', status: 'completed' });
    assert.equal((await retryReview(f.config, 53, f.io)).status, 'QUEUED');
    const queued = await f.ledger();
    assert.equal(queued.head_sha, NEXT); assert.equal(queued.worktree, callerDirectory);
    assert.deepEqual(queued.reviews, failed.reviews); assert.deepEqual(queued.last_review, previous);
    assert.equal(queued.round, 1); assert.equal(queued.history.at(-1).error, failed.error);
    assert.equal(queued.history.at(-1).worktree, callerDirectory); assert.equal(queued.history.at(-1).stage, 'prepare');
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, engine === 'multi' ? 'WAITING_REVIEW' : 'NATIVE_REVIEWING');
    assert.equal((await f.ledger()).round, 2); assert.equal(f.calls.rerun, 1);
  });
}

test('preparation retry never moves an uncorrected caller directory or bypasses its head check', async t => {
  const f = await priorityFixture(t); await f.register();
  const job = await f.ledger(); job.status = 'NEEDS_ATTENTION'; job.worktree = '/caller/old-review';
  job.error = 'review checkout head changed'; await writeFile(f.file, JSON.stringify(job));
  f.io.prepare = async pending => { assert.equal(pending.worktree, job.worktree); throw new Error('review checkout head changed'); };
  assert.equal((await retryReview(f.config, 53, f.io)).status, 'QUEUED');
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NEEDS_ATTENTION');
  assert.equal((await f.ledger()).worktree, job.worktree); assert.equal(f.calls.rerun ?? 0, 0);
});

test('undispatched preparation retry requires every issue run to be terminal and known', async t => {
  const f = await priorityFixture(t); await f.register();
  const job = await f.ledger(); job.status = 'NEEDS_ATTENTION'; job.error = 'review checkout head changed';
  await writeFile(f.file, JSON.stringify(job));
  const before = await readFile(f.file, 'utf8');
  for (const status of ['queued', 'dispatched', 'running', 'waiting_local_directory', 'unknown']) {
    f.io.runs = async () => [{ id: 'author-or-fixer', agent_id: 'coder', status }];
    await assert.rejects(retryReview(f.config, 53, f.io), /active or unknown/);
    assert.equal(await readFile(f.file, 'utf8'), before);
  }
});

test('preparation retry refuses repair, delivery, current results and ambiguous dispatch evidence', async t => {
  const f = await priorityFixture(t); await f.register();
  const clean = await f.ledger(); clean.status = 'NEEDS_ATTENTION'; clean.error = 'preparation failed';
  const evidence = [
    { native: {} }, { native: { stage: 'fix', agent_id: 'coder' } },
    { result_path: '/unknown/result.json' }, { review_url: 'https://example.com/review' },
    { reported_result_path: '/unknown/reported.json' }, { delivery_request: {} }, { last_repair: {} }, { reviewer_index: 0 },
    { reviews: [{ head_sha: HEAD, result: pass() }] }, { review_results: [{ head_sha: HEAD, result: pass() }] },
    { reviews: [{ result: pass() }] }, { last_review: { head_sha: HEAD, ...pass() } }, { last_review: pass() },
    { last_io_error: { stage: 'SENDING', message: 'unknown outcome' } }, { engine: 'codex' },
  ];
  for (const extra of evidence) {
    const candidate = JSON.stringify({ ...clean, ...extra }); await writeFile(f.file, candidate);
    await assert.rejects(retryReview(f.config, 53, f.io), /not a repair, delivery or unknown dispatch/);
    assert.equal(await readFile(f.file, 'utf8'), candidate);
  }
});

test('a wrong-head result file is rejected even when conversation text claims the correct head', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  await f.result(pass(), NEXT);
  for (const run of f.history) run.status = 'completed';
  f.io.runMessages = async () => [{ content: JSON.stringify({ head_sha: HEAD, ...pass() }) }];
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NEEDS_ATTENTION');
  assert.match((await f.ledger()).error, /another candidate/);
  assert.equal(f.calls.publish, 0); assert.equal(f.calls.merge, 0);
});
test('a matching conversation verdict cannot replace the authoritative result file', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  for (const run of f.history) run.status = 'completed';
  f.io.runMessages = async () => [{ content: ['```json', JSON.stringify({ head_sha: HEAD, ...pass() }), '```'].join('\n') }];
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NEEDS_ATTENTION');
  await assert.rejects(readFile((await f.ledger()).result_path), { code: 'ENOENT' });
  assert.equal(f.calls.merge, 0);
});
test('a timed-out live reviewer retains its slot and cannot be retried', async t => {
  const f = await priorityFixture(t); await f.register();
  const old = sibling(54, 'gptweb', 'reviewer-a'); old.status = 'NEEDS_ATTENTION';
  await writeFile(path.join(f.dir, 'review-queue/pr-54.json'), JSON.stringify(old));
  const realRuns = f.io.runs;
  f.io.runs = async id => id === old.issue_id ? [{ id: 'still-running', agent_id: 'reviewer-a', status: 'running' }] : realRuns(id);
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-b');
  await assert.rejects(retryReview(f.config, 54, f.io), /still active/);
});
test('reviewer aliases share browser capacity and live bridge admission overrides free ledgers', async t => {
  const f = await priorityFixture(t, [
    { key: 'web-a', agent_id: 'reviewer-a', capacity_key: 'browser' },
    { key: 'local', agent_id: 'reviewer-b' },
  ]);
  f.config.review.capacity_resources = { browser: { max_concurrency: 1, status_url: 'http://127.0.0.1/status' } };
  f.io.capacity = async () => ({ capacity: 1, busy: true }); await f.register();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-b');
});
test('context rejects another agent or another run before revealing its result path', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  await assert.rejects(reviewContext(f.config, f.issue.id, f.io, { agent_id: 'reviewer-b' }), /another reviewer/);
  await assert.rejects(reviewContext(f.config, f.issue.id, f.io, { run_id: 'old-run' }), /another run/);
  assert.equal((await reviewContext(f.config, f.issue.id, f.io, { agent_id: 'reviewer-a', run_id: f.history[0].id })).head_sha, HEAD);
});
test('connector context uses a stable allowed root and rejects paths outside it', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.ledger(); job.worktree = path.join(f.dir, 'worktrees/pr-53'); await writeFile(f.file, JSON.stringify(job));
  const context = await reviewContext(f.config, f.issue.id, f.io, { workspace_root: f.dir });
  assert.equal(context.worktree_relative_path, path.join('worktrees', 'pr-53'));
  await assert.rejects(reviewContext(f.config, f.issue.id, f.io, { workspace_root: path.join(f.dir, 'another-root') }), /outside the connector workspace/);
});
test('time waiting in the platform queue does not consume the model execution timeout', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.ledger(); job.native.requested_at = '2020-01-01T00:00:00.000Z'; await writeFile(f.file, JSON.stringify(job));
  for (const run of f.history) run.status = 'waiting_local_directory';
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
});

test('a late same-head result is consumed after the waiting budget without another dispatch', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.ledger(); job.native.requested_at = '2020-01-01T00:00:00.000Z'; await writeFile(f.file, JSON.stringify(job));
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert((await f.ledger()).native.waiting_budget_exceeded_at);
  await f.result(); assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  assert.equal(f.calls.rerun, 1);
});
test('text without a head cannot create a result file or authorize delivery', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  for (const run of f.history) run.status = 'completed';
  f.io.runMessages = async () => [{ content: ['```json', JSON.stringify(pass()), '```'].join('\n') }];
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'NEEDS_ATTENTION');
  assert.equal(f.calls.merge, 0);
});

test('text_unavailable does not invalidate an existing authoritative same-head file', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.result(); const original = await readFile(job.result_path, 'utf8');
  for (const run of f.history) { run.status = 'failed'; run.error = 'text_unavailable: Unable to load this response'; }
  f.io.runMessages = async () => [{ type: 'error', content: 'text_unavailable: Unable to load this response' }];
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  assert.equal((await f.ledger()).native.channel_state, 'text_unavailable');
  assert.equal(await readFile(job.result_path, 'utf8'), original);
});
test('missing file with text_unavailable is clearly logged and never filled from text', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  for (const run of f.history) { run.status = 'failed'; run.error = 'text_unavailable'; }
  const result = await runQueuedReview(f.config, 53, f.io);
  assert.equal(result.status, 'NEEDS_ATTENTION'); assert.match(result.reason, /text_unavailable/);
  assert.equal((await f.ledger()).native.channel_state, 'text_unavailable');
  await assert.rejects(readFile((await f.ledger()).result_path), { code: 'ENOENT' });
  assert.equal(f.calls.merge, 0);
});

test('old queued priority snapshots retain the occupied agent limit after upgrade', async t => {
  const f = await priorityFixture(t); await f.register();
  const job = await f.ledger();
  for (const reviewer of job.reviewers) delete reviewer.capacity_key;
  await writeFile(f.file, JSON.stringify(job));
  await writeFile(path.join(f.dir, 'review-queue/pr-54.json'), JSON.stringify(sibling(54, 'gptweb', 'reviewer-a')));
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-b');
});

test('a legacy pending snapshot cannot bypass a busy current browser mapping', async t => {
  const f = await priorityFixture(t);
  f.config.review.reviewers[0].capacity_key = 'browser';
  f.config.review.capacity_resources = { browser: { max_concurrency: 1, status_url: 'http://bridge/status' } };
  let probes = 0;
  f.io.capacity = async () => { probes++; return { capacity: 1, busy: true }; };
  await f.register();
  const job = await f.ledger(); delete job.reviewers[0].capacity_key;
  job.reviewers[0].max_concurrency = 3;
  await writeFile(f.file, JSON.stringify(job));
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-b'); assert.equal(probes, 1);
  assert.equal((await f.ledger()).reviewers[0].capacity_key, undefined, 'capacity resolution does not rewrite the frozen panel');
});

test('a legacy sibling reserves its current shared browser before the bridge becomes busy', async t => {
  const f = await priorityFixture(t);
  f.config.review.reviewers[0].capacity_key = 'browser';
  f.config.review.capacity_resources = { browser: { max_concurrency: 1, status_url: 'http://bridge/status' } };
  f.io.capacity = async () => ({ capacity: 1, busy: false });
  await f.register();
  await writeFile(path.join(f.dir, 'review-queue/pr-54.json'), JSON.stringify(sibling(54, 'gptweb', 'reviewer-a')));
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-b');
});

test('an explicit frozen group change waits without reinterpreting the old reviewer', async t => {
  const f = await priorityFixture(t, [{ key: 'gptweb', agent_id: 'reviewer-a', capacity_key: 'browser' }]);
  f.config.review.capacity_resources = { browser: { max_concurrency: 1, status_url: 'http://bridge/status' } };
  f.io.capacity = async () => ({ capacity: 1, busy: false });
  await f.register();
  const job = await f.ledger(); job.reviewers[0].capacity_key = 'old-browser';
  await writeFile(f.file, JSON.stringify(job));
  const result = await runQueuedReview(f.config, 53, f.io);
  assert.equal(result.status, 'WAITING_CAPACITY'); assert.match(result.reason, /mapping changed/);
  assert.equal((await f.ledger()).reviewers[0].capacity_key, 'old-browser');
  assert.equal(f.calls.rerun ?? 0, 0);
});

for (const stage of ['NATIVE_REVIEWING', 'WAITING_CAPACITY']) {
  test(`a sibling with an explicit old group reserves both groups while ${stage}`, async t => {
    const f = await priorityFixture(t);
    f.config.review.reviewers[0].capacity_key = 'browser';
    f.config.review.capacity_resources = { browser: { max_concurrency: 1, status_url: 'http://bridge/status' } };
    f.io.capacity = async () => ({ capacity: 1, busy: false });
    await f.register();
    const old = sibling(54, 'gptweb', 'reviewer-a'); old.status = stage;
    old.reviewers[0] = { ...old.reviewers[0], agent_id: 'reviewer-a', capacity_key: 'old-browser' };
    await writeFile(path.join(f.dir, 'review-queue/pr-54.json'), JSON.stringify(old));
    if (stage === 'WAITING_CAPACITY') f.io.runs = async () => [];
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
    assert.equal(f.issue.assignee_id, 'reviewer-b');
  });
}

test('an ambiguous current mapping cannot give a legacy reviewer multiple resource slots', async t => {
  const f = await priorityFixture(t);
  f.config.review.reviewers[0].capacity_key = 'browser';
  f.config.review.reviewers.push({ key: 'web-alias', agent_id: 'reviewer-a', capacity_key: 'other-browser' });
  await f.register();
  const job = await f.ledger(); delete job.reviewers[0].capacity_key;
  await writeFile(f.file, JSON.stringify(job));
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal(f.issue.assignee_id, 'reviewer-b');
});

for (const mode of ['all', 'first_pass']) {
  test(`${mode}: initial busy capacity waits in the same round and resumes once`, async t => {
    const f = await multiFixture(t, { mode });
    f.config.review.reviewers[0].capacity_key = 'browser';
    f.config.review.capacity_resources = { browser: { max_concurrency: 1, status_url: 'http://bridge/status' } };
    let busy = true;
    f.io.capacity = async () => ({ capacity: 1, busy });
    await f.register();
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_CAPACITY');
    assert.equal((await f.ledger()).round, 1);
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_CAPACITY');
    assert.equal((await f.ledger()).round, 1);
    assert.equal(f.calls.rerun ?? 0, 0);
    busy = false;
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
    assert.equal((await f.ledger()).round, 1);
    assert.equal(f.calls.rerun, 1);
  });

  test(`${mode}: a following alias waits for the actual prior run and preserves accepted results`, async t => {
    const f = await multiFixture(t, { mode });
    for (const reviewer of f.config.review.reviewers) reviewer.capacity_key = 'browser';
    f.config.review.capacity_resources = { browser: { max_concurrency: 1, status_url: 'http://bridge/status' } };
    let busy = false;
    f.io.capacity = async () => ({ capacity: 1, busy });
    await f.register(); await runQueuedReview(f.config, 53, f.io);
    const first = await f.result(mode === 'all' ? pass() : { ...pass(), verdict: 'BLOCKED', checks: [] });
    const firstBytes = await readFile(first.result_path, 'utf8');
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_CAPACITY');
    let waiting = await f.ledger();
    assert.equal(waiting.round, 1); assert.equal(waiting.review_results.length, 1);
    assert.equal(waiting.reviews.length, 1); assert.equal(waiting.result_path, first.result_path);
    assert.equal(f.calls.rerun, 1, 'an active prior run owns its physical slot even if the bridge reports idle');
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_CAPACITY');
    f.history[0].status = 'completed'; busy = true;
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_CAPACITY');
    assert.equal((await f.ledger()).reviews.length, 1);
    busy = false;
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
    waiting = await f.ledger();
    assert.equal(waiting.round, 1); assert.equal(waiting.review_results.length, 1);
    assert.match(waiting.result_path, /second-review\.json$/);
    assert.equal(f.calls.rerun, 2);
    assert.equal(await readFile(first.result_path, 'utf8'), firstBytes);
    await f.result();
    assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
    assert.equal((await f.ledger()).review_results.length, 2);
    assert.equal(f.calls.merge, 1);
  });
}

test('recovering an unattempted dispatch rechecks capacity without replacing its intent', async t => {
  const f = await priorityFixture(t); await f.register();
  const assign = f.io.assign;
  f.io.assign = async () => { throw disconnect(); };
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'RETRY_IO');
  const pending = await f.ledger();
  await writeFile(path.join(f.dir, 'review-queue/pr-54.json'), JSON.stringify(sibling(54, 'gptweb', 'reviewer-a')));
  f.io.assign = assign;
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_CAPACITY');
  const waiting = await f.ledger();
  assert.deepEqual(waiting.native, pending.native);
  assert.equal(waiting.result_path, pending.result_path);
  assert.equal(f.calls.rerun ?? 0, 0);
});

test('multi repair dispatch recovery keeps its fixer stage without taking a reviewer slot', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  await f.result(changes());
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'REPAIR_READY');
  f.history[0].status = 'completed';
  const assign = f.io.assign;
  f.io.assign = async () => { throw disconnect(); };
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'RETRY_IO');
  assert.equal((await f.ledger()).native.stage, 'fix');
  f.io.assign = assign;
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'AWAITING_FIX');
  assert.equal(f.issue.assignee_id, 'coder');
  assert.equal(f.calls.rerun, 2);
});

async function publicResults(t, f) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'public-review-results-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  f.config.review.result_root = path.join(root, 'results');
  f.config.review.result_workspace_root = root;
  return root;
}

for (const engine of ['multica', 'multi']) {
  test(`${engine}: new results use a separate configured directory`, async t => {
    const f = engine === 'multi' ? await priorityFixture(t) : await nativeFixture(t);
    f.config.review.engine = engine;
    const root = await publicResults(t, f);
    await f.register(); await runQueuedReview(f.config, 53, f.io);
    const job = await f.ledger();
    assert.equal(job.result_directory, path.join(root, 'results/pr-53'));
    assert(job.result_path.startsWith(job.result_directory + path.sep));
    const context = await reviewContext(f.config, f.issue.id, f.io);
    assert.equal(context.result_workspace_root, root);
    assert.equal(context.result_relative_path, path.relative(root, job.result_path));
    assert.equal(path.resolve(root, context.result_relative_path), job.result_path);
    await f.result(); assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  });
}

test('an existing job keeps its legacy result path when result roots are configured later', async t => {
  const f = await priorityFixture(t); await f.register(); await runQueuedReview(f.config, 53, f.io);
  const original = await f.ledger();
  assert.equal(path.dirname(original.result_path), path.join(f.dir, 'review-queue/results/pr-53'));
  await publicResults(t, f);
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'WAITING_REVIEW');
  assert.equal((await f.ledger()).result_path, original.result_path);
  await assert.rejects(reviewContext(f.config, f.issue.id, f.io), /outside its configured workspace/);
  await f.result(); assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  assert.equal((await f.ledger()).result_path, original.result_path);
});

test('a registered result directory stays fixed before dispatch even if the config changes', async t => {
  const f = await priorityFixture(t);
  const root = await publicResults(t, f); await f.register();
  f.config.review.result_root = path.join(root, 'changed-results');
  await runQueuedReview(f.config, 53, f.io);
  assert.equal(path.dirname((await f.ledger()).result_path), path.join(root, 'results/pr-53'));
});

test('an old queued job and its following reviewers keep the legacy directory after a root change', async t => {
  const f = await multiFixture(t); await f.register();
  await publicResults(t, f);
  await runQueuedReview(f.config, 53, f.io);
  const first = await f.result();
  const legacyDir = path.join(f.dir, 'review-queue/results/pr-53');
  assert.equal(path.dirname(first.result_path), legacyDir);
  await runQueuedReview(f.config, 53, f.io);
  assert.equal(path.dirname((await f.ledger()).result_path), legacyDir);
  await f.result();
  assert.equal((await runQueuedReview(f.config, 53, f.io)).status, 'DONE');
  const completed = await f.ledger();
  assert.equal(path.dirname(completed.result_path), legacyDir);
  assert(completed.result_path.endsWith('-combined-review.json'));
});

test('a result_root alone becomes the connector result workspace', async t => {
  const f = await priorityFixture(t); await publicResults(t, f);
  delete f.config.review.result_workspace_root;
  await f.register(); await runQueuedReview(f.config, 53, f.io);
  const context = await reviewContext(f.config, f.issue.id, f.io);
  assert.equal(context.result_workspace_root, f.config.review.result_root);
  assert.equal(context.result_relative_path, path.join('pr-53', path.basename(context.result_path)));
});

test('result roots reject relative configuration and context paths escaping the authorized root', async t => {
  const f = await priorityFixture(t);
  for (const key of ['result_root', 'result_workspace_root']) {
    f.config.review[key] = 'relative/path';
    await assert.rejects(f.register(), /must be absolute/);
    delete f.config.review[key];
  }
  await f.register(); await runQueuedReview(f.config, 53, f.io);
  const job = await f.ledger();
  for (const outside of [path.dirname(f.dir), path.join(path.dirname(f.dir), 'outside-review.json')]) {
    job.result_path = outside; await writeFile(f.file, JSON.stringify(job));
    await assert.rejects(reviewContext(f.config, f.issue.id, f.io), /outside its configured workspace/);
  }
});
