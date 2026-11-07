import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { command } from './local.mjs';
import { makeReviewIO, registerGitReview, reviewContext, reconcileReviews, retryReview } from './review-queue.mjs';

const W = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const I = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const P = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const R = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const F = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';
const ref = 'refs/heads/candidate';

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'review-git-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const author = path.join(root, 'author');
  const checkout = path.join(root, 'review');
  const remote = path.join(root, 'remote.git');
  const git = (args, cwd = author) => command('git', args, cwd);
  await git(['init', '-b', 'main', author], root);
  const commit = async text => {
    await writeFile(path.join(author, 'data.txt'), text);
    await git(['add', 'data.txt']);
    await git(['-c', 'user.email=fixture@example.invalid', '-c', 'user.name=Fixture', '-c', 'commit.gpgsign=false', 'commit', '-m', text]);
  };
  await commit('base');
  await git(['clone', '--bare', author, remote], root);
  await git(['clone', remote, checkout], root);
  await git(['remote', 'add', 'origin', remote]);
  await git(['checkout', '-b', 'candidate']);
  await commit('candidate');
  await git(['push', 'origin', ref]);

  const config = { repository: 'demo', repository_path: checkout, state_dir: path.join(root, 'state'),
    workspace_id: W, project_id: P, reviewer_id: R, fixer_id: F, multica_path: 'multica-test',
    review: { enabled: true, candidate_source: 'git', engine: 'multi', mode: 'priority',
      reviewers: [{ key: 'main', agent_id: R }] } };
  const issue = { id: I, number: 7, identifier: 'DEMO-7', title: 'Test', description: 'Acceptance A',
    workspace_id: W, project_id: P, status: 'in_review' };
  const history = [], writes = [];
  let ambiguous = false;
  const io = makeReviewIO(config, async (file, args, cwd) => {
    if (file === 'git') return command(file, args, cwd);
    assert.equal(file, 'multica-test', 'standalone mode must never call gh or another service');
    assert.deepEqual(args.slice(0, 2), ['--workspace-id', W]);
    assert.equal(args[2], 'issue');
    const action = args[3];
    if (action === 'get') {
      assert([I, issue.identifier].includes(args[4]));
      return JSON.stringify(issue);
    }
    assert.equal(args[4], I);
    if (action === 'runs') return JSON.stringify(history);
    if (action === 'assign') {
      assert(args.includes('--no-start'));
      issue.assignee_id = args[args.indexOf('--to-id') + 1];
      writes.push([action, issue.assignee_id]);
    } else if (action === 'rerun') {
      writes.push([action, issue.assignee_id]);
      if (ambiguous) throw new Error('dispatch acknowledgement was lost');
      history.push({ id: 'run-' + history.length, agent_id: issue.assignee_id, status: 'running' });
    } else assert.fail('unexpected mutation: ' + action);
    return '{}';
  });
  return { config, issue, io, writes, history, git, commit, checkout,
    ambiguous: () => { ambiguous = true; },
    register: () => registerGitReview(config, issue.identifier, ref, io),
    reconcile: async () => (await reconcileReviews(config, io)).tasks[0]?.status };
}

async function writeVerdict(f, verdict, base = null) {
  const ctx = await reviewContext(f.config, f.issue.id, f.io, { agent_id: R, run_id: f.history.at(-1).id });
  assert.equal(ctx.git_ref, ref);
  assert.equal((await f.git(['rev-parse', 'HEAD'], ctx.worktree)).trim(), ctx.head_sha);
  assert.equal((await f.git(['diff', ctx.base_sha + '...' + ctx.head_sha, '--name-only'], ctx.worktree)).trim(), 'data.txt');
  await writeFile(ctx.result_path, JSON.stringify({ head_sha: ctx.head_sha, base_sha: base ?? ctx.base_sha, verdict,
    summary: verdict, findings: verdict === 'CHANGES_REQUIRED' ? [{ severity: 'P2', problem: 'repair needed' }] : [],
    checks: [{ command: 'git diff', status: 'PASS', exit_code: 0 }], limitations: [] }));
  f.history.at(-1).status = 'completed';
  return ctx;
}

test('a normal Git remote and Multica alone support review, same-ticket repair, and PASS without closing', async t => {
  const f = await fixture(t);
  f.history.push({ id: 'author-1', agent_id: F, status: 'running' });
  await f.register();
  assert.equal(await f.reconcile(), 'WAITING_AUTHOR');
  assert.deepEqual(f.writes, []);
  f.history[0].status = 'completed';
  assert.equal(await f.reconcile(), 'WAITING_REVIEW');
  const first = await writeVerdict(f, 'CHANGES_REQUIRED');
  assert.equal(await f.reconcile(), 'REPAIR_READY');
  assert.equal(await f.reconcile(), 'AWAITING_FIX');
  assert.equal((await reviewContext(f.config, I, f.io, { agent_id: F })).previous_review.verdict, 'CHANGES_REQUIRED');
  await f.commit('repaired');
  await f.git(['push', 'origin', ref]);
  await f.register();
  assert.equal(await f.reconcile(), 'WAITING_AUTHOR');
  f.history.at(-1).status = 'completed';
  assert.equal(await f.reconcile(), 'WAITING_REVIEW');
  const next = await writeVerdict(f, 'PASS');
  assert.notEqual(next.head_sha, first.head_sha);
  assert.notEqual(next.result_path, first.result_path);
  assert.equal(await f.reconcile(), 'PASSED');
  assert.equal(await f.reconcile(), undefined);
  assert.equal(f.issue.status, 'in_review');
  assert.deepEqual(f.writes, [['assign', R], ['rerun', R], ['assign', F], ['rerun', F], ['assign', R], ['rerun', R]]);
});

test('base movement invalidates a matching-head result and permits a fresh registration', async t => {
  const f = await fixture(t);
  await f.register();
  assert.equal(await f.reconcile(), 'WAITING_REVIEW');
  const old = await writeVerdict(f, 'PASS');
  await f.git(['checkout', 'main']);
  await f.commit('base moved');
  await f.git(['push', 'origin', 'refs/heads/main']);
  assert.equal(await f.reconcile(), 'CANDIDATE_CHANGED');
  await f.register();
  assert.equal(await f.reconcile(), 'WAITING_REVIEW');
  const next = await writeVerdict(f, 'PASS', old.base_sha);
  assert.equal(next.head_sha, old.head_sha);
  assert.notEqual(next.base_sha, old.base_sha);
  assert.notEqual(next.result_path, old.result_path);
  assert.equal(await f.reconcile(), 'NEEDS_ATTENTION');
});

test('scope and acceptance changes block dispatch before any Multica write', async t => {
  const f = await fixture(t);
  f.issue.workspace_id = 'wrong';
  await assert.rejects(f.register(), /outside mapped/);
  f.issue.workspace_id = W;
  await f.register();
  f.issue.description = 'Acceptance B';
  assert.equal(await f.reconcile(), 'CANDIDATE_CHANGED');
  assert.deepEqual(f.writes, []);
});

test('an unconfirmed Multica rerun does not repeat on reconcile or operator retry', async t => {
  const f = await fixture(t);
  f.ambiguous();
  await f.register();
  await f.reconcile();
  for (let i = 0; i < 4; i++) await f.reconcile();
  const file = path.join(f.config.state_dir, 'git-review-queue', 'pr-7.json');
  const job = JSON.parse(await readFile(file, 'utf8'));
  assert.equal(job.status, 'NATIVE_REVIEWING');
  job.native.requested_at = '2000-01-01T00:00:00Z';
  await writeFile(file, JSON.stringify(job));
  assert.equal(await f.reconcile(), 'NEEDS_ATTENTION');
  await assert.rejects(retryReview(f.config, 7, f.io), /outcome is unknown/);
  assert.deepEqual(f.writes, [['assign', R], ['rerun', R]]);
});
