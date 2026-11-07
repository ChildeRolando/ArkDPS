import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveReviewers, combineReviews, pendingReviewer, reviewerSnapshot, reviewerArtifact, reviewerCapacityKeys, selectPriorityReviewer } from './reviewers.mjs';

const HEAD = 'a'.repeat(40);
const pass = (extra = {}) => ({ head_sha: HEAD, verdict: 'PASS', summary: '接受条件通过', findings: [],
  checks: [{ command: 'npm test', status: 'PASS', exit_code: 0 }], limitations: [], ...extra });
const changes = (extra = {}) => ({ head_sha: HEAD, verdict: 'CHANGES_REQUIRED', summary: '有阻断',
  findings: [{ id: 'R1', severity: 'P2', path: 'src/a.ts', line: 3, problem: 'p', evidence: 'e', fix: 'f' }],
  checks: [{ command: 'npm test', status: 'PASS', exit_code: 0 }], limitations: [], ...extra });
const entry = (key, result, extra = {}) => ({ key, label: key.toUpperCase(), agent_id: `agent-${key}`, required: true, result, ...extra });

const config = reviewers => ({ review: { reviewers }, fixer_id: 'fixer' });
test('different agents sharing one browser cannot manufacture extra capacity', () => {
  const c = config([{ key: 'a', agent_id: 'agent-a', capacity_key: 'browser', max_concurrency: 3 },
    { key: 'b', agent_id: 'agent-b', capacity_key: 'browser', max_concurrency: 2 }]);
  c.review.capacity_resources = { browser: { max_concurrency: 1 } };
  const { reviewers } = resolveReviewers(c);
  assert(reviewers.every(reviewer => reviewer.max_concurrency === 1));
  assert.equal(selectPriorityReviewer(reviewers, { browser: 1 }), null);
});

test('resolveReviewers keeps order, drops disabled reviewers and defaults to mode=all', () => {
  const resolved = resolveReviewers(config([
    { key: 'primary', agent_id: 'a1', label: 'Primary' },
    { key: 'off', agent_id: 'a2', enabled: false },
    { key: 'second', agent_id: 'a3', required: false },
  ]));
  assert.equal(resolved.mode, 'all');
  assert.deepEqual(resolved.reviewers.map(item => item.key), ['primary', 'second']);
  assert.deepEqual(reviewerSnapshot(resolved), [
    { key: 'primary', label: 'Primary', agent_id: 'a1', required: true, max_concurrency: 1, capacity_key: 'a1' },
    { key: 'second', label: 'second', agent_id: 'a3', required: false, max_concurrency: 1, capacity_key: 'a3' },
  ]);
});

test('resolveReviewers rejects unsafe or ambiguous reviewer sets', () => {
  assert.throws(() => resolveReviewers({ review: {} }), /non-empty array/);
  assert.throws(() => resolveReviewers(config([{ key: 'Bad Key', agent_id: 'a' }])), /needs a key/);
  assert.throws(() => resolveReviewers(config([{ key: 'a', agent_id: 'x' }, { key: 'a', agent_id: 'y' }])), /duplicate reviewer key/);
  assert.throws(() => resolveReviewers(config([{ key: 'a' }])), /needs an agent_id/);
  assert.throws(() => resolveReviewers(config([{ key: 'a', agent_id: 'a', enabled: false }])), /no enabled reviewer/);
  assert.throws(() => resolveReviewers(config([{ key: 'a', agent_id: 'a', required: false }])), /must be required/);
  assert.throws(() => resolveReviewers({ review: { mode: 'majority', reviewers: [{ key: 'a', agent_id: 'a' }] } }), /review.mode/);
  assert.throws(() => resolveReviewers({ fixer_id: 'fixer', review: { reviewers: [{ key: 'a', agent_id: 'fixer' }] } }), /is the fixer/);
});

test('combineReviews: all required PASS produces a deliverable PASS', () => {
  const combined = combineReviews([entry('primary', pass()), entry('second', pass())], { headSha: HEAD });
  assert.equal(combined.verdict, 'PASS');
  assert.equal(combined.head_sha, HEAD);
  assert.equal(combined.checks.length, 1, 'identical checks are deduplicated');
  assert.deepEqual(combined.reviewers.map(item => item.key), ['primary', 'second']);
  assert.match(combined.summary, /PRIMARY: PASS/);
});

test('combineReviews: one CHANGES_REQUIRED wins and findings carry the reviewer key', () => {
  const combined = combineReviews([entry('primary', pass()), entry('second', changes())], { headSha: HEAD });
  assert.equal(combined.verdict, 'CHANGES_REQUIRED');
  assert.deepEqual(combined.findings.map(item => [item.reviewer, item.id]), [['second', 'second/R1']]);
});

test('combineReviews: BLOCKED outranks CHANGES_REQUIRED in mode=all', () => {
  const blocked = { head_sha: HEAD, verdict: 'BLOCKED', summary: '环境阻止', findings: [], checks: [], limitations: ['gptwb down'] };
  const combined = combineReviews([entry('primary', changes()), entry('second', blocked)], { headSha: HEAD });
  assert.equal(combined.verdict, 'BLOCKED');
  assert.match(combined.limitations.join('\n'), /\[second\] gptwb down/);
});

test('combineReviews: first_pass accepts one PASS and records the skipped reviewer', () => {
  const combined = combineReviews([entry('primary', pass())], { mode: 'first_pass', headSha: HEAD, skipped: ['second'] });
  assert.equal(combined.verdict, 'PASS');
  assert.match(combined.limitations.join('\n'), /\[second\] skipped/);
});

test('combineReviews refuses a PASS that carries a blocking finding or a failed check', () => {
  assert.throws(() => combineReviews([entry('primary', pass({ findings: changes().findings }))], { headSha: HEAD }), /blocking finding/);
  assert.throws(() => combineReviews([entry('primary', pass({ checks: [{ command: 'npm test', status: 'FAIL', exit_code: 1 }] }))], { headSha: HEAD }), /failed or missing check/);
});

test('combineReviews rejects a result from another candidate', () => {
  assert.throws(() => combineReviews([entry('primary', pass({ head_sha: 'b'.repeat(40) }))], { headSha: HEAD }), /another candidate/);
});

test('pendingReviewer walks the configured order and ignores finished reviewers', () => {
  const reviewers = [{ key: 'a', required: true }, { key: 'b', required: true }, { key: 'c', required: false }];
  assert.equal(pendingReviewer(reviewers, [], HEAD).key, 'a');
  assert.equal(pendingReviewer(reviewers, [{ key: 'a', head_sha: HEAD }], HEAD).key, 'b');
  assert.equal(pendingReviewer(reviewers, [{ key: 'a', head_sha: HEAD }, { key: 'b', head_sha: HEAD }], HEAD).key, 'c');
  assert.equal(pendingReviewer(reviewers, reviewers.map(item => ({ key: item.key, head_sha: HEAD })), HEAD), null);
  assert.equal(pendingReviewer(reviewers, [{ key: 'a', head_sha: 'b'.repeat(40) }], HEAD).key, 'a', 'another head does not count');
});

test('reviewerArtifact keeps reviewer results apart inside one round', () => {
  const dir = 'C:/state/results/pr-7';
  assert.match(reviewerArtifact(dir, 2, HEAD, 'primary'), /2-a{40}-primary-review\.json$/);
  assert.match(reviewerArtifact(dir, 2, HEAD, 'combined'), /combined-review\.json$/);
});

test('priority mode needs no required reviewer and defaults capacity to one', () => {
  const resolved = resolveReviewers({ review: { mode: 'priority', reviewers: [
    { key: 'gptweb', agent_id: 'a1' },
    { key: 'local', agent_id: 'a2', max_concurrency: 2 },
  ] } });
  assert.equal(resolved.mode, 'priority');
  assert.deepEqual(resolved.reviewers.map(item => [item.key, item.max_concurrency]), [['gptweb', 1], ['local', 2]]);
  assert.deepEqual(reviewerSnapshot(resolved).map(item => item.key), ['gptweb', 'local']);
});

test('selectPriorityReviewer takes the first free slot and respects limits', () => {
  const reviewers = [{ key: 'gptweb', max_concurrency: 1 }, { key: 'local', max_concurrency: 2 }];
  assert.equal(selectPriorityReviewer(reviewers, {}).key, 'gptweb');
  assert.equal(selectPriorityReviewer(reviewers, { gptweb: 1 }).key, 'local');
  assert.equal(selectPriorityReviewer(reviewers, { gptweb: 1, local: 1 }).key, 'local');
  assert.equal(selectPriorityReviewer(reviewers, { gptweb: 1, local: 2 }), null);
  assert.equal(selectPriorityReviewer([{ key: 'x' }], { x: 1 }), null, 'missing limit means one');
});

test('old reviewer snapshots use their agent identity for capacity', () => {
  const reviewers = [{ key: 'web', agent_id: 'agent-web' }, { key: 'local', agent_id: 'agent-local' }];
  assert.equal(selectPriorityReviewer(reviewers, { 'agent-web': 1 }).key, 'local');
  assert.equal(selectPriorityReviewer([{ key: 'old-key-only' }], { 'old-key-only': 1 }), null);
});

test('old snapshots inherit the unique current physical resource and its limit', () => {
  const c = config([{ key: 'web', agent_id: 'agent-web', capacity_key: 'browser' }]);
  c.review.capacity_resources = { browser: { max_concurrency: 1, status_url: 'http://bridge/status' } };
  const old = { key: 'web', agent_id: 'agent-web', max_concurrency: 3 };
  assert.deepEqual(reviewerCapacityKeys(old, c), ['browser']);
  assert.equal(selectPriorityReviewer([old], { browser: 1 }, c), null);
  assert.equal(selectPriorityReviewer([old], { browser: 0 }, c), old);
});

test('explicit group changes and ambiguous agent mappings cannot create free slots', () => {
  const c = config([{ key: 'web', agent_id: 'agent-web', capacity_key: 'browser' }]);
  const old = { key: 'web', agent_id: 'agent-web', capacity_key: 'old-browser' };
  assert.deepEqual(reviewerCapacityKeys(old, c), ['old-browser', 'browser']);
  assert.equal(selectPriorityReviewer([old], {}, c), null);
  c.review.reviewers.push({ key: 'alias', agent_id: 'agent-web', capacity_key: 'other-browser' });
  assert.equal(selectPriorityReviewer([{ key: 'legacy', agent_id: 'agent-web' }], {}, c), null);
});
