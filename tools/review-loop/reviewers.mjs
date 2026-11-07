// Multi-reviewer registry for the review queue.
//
// A reviewer is identified by a stable `key` and is dispatched to a Multica agent
// (`agent_id`). The queue does not care which runtime backs that agent: the
// production Reviewer, a Codex/Claude agent, or the ChatGPT-Web runtime shim are
// all the same shape here.
//
// Backwards compatibility: a config without `review.reviewers` keeps using the
// legacy single-reviewer fields (`review.reviewer_id` / `review.engine`) and never
// reaches this module.
import assert from 'node:assert/strict';
import path from 'node:path';

export const REVIEWER_KEY = /^[a-z0-9][a-z0-9-]{0,31}$/;
export const MULTI_ENGINE = 'multi';
export const REVIEW_MODES = ['all', 'first_pass', 'priority'];

// Validates the configured reviewer set and returns the enabled reviewers in
// dispatch order plus the aggregation mode.
export function resolveReviewers(config) {
  const configured = config?.review?.reviewers;
  assert(Array.isArray(configured) && configured.length, 'review.reviewers must be a non-empty array for the multi engine');

  const seen = new Set();
  const all = configured.map((raw, index) => {
    const key = String(raw?.key ?? '').trim();
    assert(REVIEWER_KEY.test(key), `reviewer #${index + 1} needs a key matching ${REVIEWER_KEY}`);
    assert(!seen.has(key), `duplicate reviewer key: ${key}`);
    seen.add(key);
    const enabled = raw?.enabled !== false;
    const agentId = String(raw?.agent_id ?? '').trim();
    if (enabled) assert(agentId, `reviewer ${key} needs an agent_id`);
    const limit = Number.isSafeInteger(raw?.max_concurrency) && raw.max_concurrency > 0 ? raw.max_concurrency : 1;
    const capacityKey = String(raw?.capacity_key ?? agentId).trim();
    const resourceLimit = config.review?.capacity_resources?.[capacityKey]?.max_concurrency;
    return {
      key,
      label: String(raw?.label ?? key),
      agent_id: agentId,
      required: raw?.required !== false,
      enabled,
      max_concurrency: Number.isSafeInteger(resourceLimit) && resourceLimit > 0 ? Math.min(limit, resourceLimit) : limit,
      capacity_key: capacityKey,
    };
  });

  const reviewers = all.filter(reviewer => reviewer.enabled);
  assert(reviewers.length, 'no enabled reviewer');

  const mode = String(config?.review?.mode ?? 'all');
  assert(REVIEW_MODES.includes(mode), `review.mode must be one of ${REVIEW_MODES.join(', ')}`);
  // priority mode picks one reviewer per round, so `required` carries no meaning.
  if (mode !== 'priority') assert(reviewers.some(reviewer => reviewer.required), 'at least one enabled reviewer must be required');

  const fixer = String(config?.fixer_id ?? '').trim();
  if (fixer) for (const reviewer of reviewers) {
    assert(reviewer.agent_id !== fixer, `reviewer ${reviewer.key} is the fixer; review and repair must be independent`);
  }

  return { reviewers, mode };
}

// Snapshot stored on the job so later config edits cannot change an in-flight review.
export function reviewerSnapshot(resolved) {
  return resolved.reviewers.map(({ key, label, agent_id, required, max_concurrency, capacity_key }) => ({ key, label, agent_id, required, max_concurrency, capacity_key }));
}

// Old snapshots did not name a physical resource. Resolve that missing fact from
// the current agent mapping, without changing the frozen panel. Explicit old
// groups remain visible: a conflicting mapping must not create a second slot.
export function reviewerCapacityKeys(reviewer, config = {}) {
  const mapped = (config.review?.reviewers ?? [])
    .filter(item => reviewer.agent_id && String(item.agent_id ?? '').trim() === reviewer.agent_id)
    .map(item => String(item.capacity_key ?? item.agent_id ?? item.key).trim());
  const keys = [...(reviewer.capacity_key != null ? [reviewer.capacity_key] : []), ...mapped];
  return [...new Set(keys.length ? keys : [reviewer.agent_id ?? reviewer.key])];
}

// Highest-priority reviewer that still has a free slot. Order is the configured
// array order; a reviewer without an explicit limit is treated as one at a time.
export function selectPriorityReviewer(reviewers, inFlight = {}, config = {}) {
  for (const reviewer of reviewers ?? []) {
    const keys = reviewerCapacityKeys(reviewer, config);
    if (keys.length !== 1) continue; // Restore the mapping or drain an explicit group change.
    let limit = Number.isSafeInteger(reviewer.max_concurrency) && reviewer.max_concurrency > 0 ? reviewer.max_concurrency : 1;
    const resource = config.review?.capacity_resources?.[keys[0]];
    if (Number.isSafeInteger(resource?.max_concurrency) && resource.max_concurrency > 0) limit = Math.min(limit, resource.max_concurrency);
    if (resource?.status_url) limit = Math.min(limit, 1); // The browser status contract has capacity=1.
    const active = Number(inFlight?.[keys[0]] ?? 0);
    if (active < limit) return reviewer;
  }
  return null;
}

export function reviewerArtifact(directory, round, headSha, key, suffix = 'review') {
  return path.join(directory, `${round}-${headSha}-${key}-${suffix}.json`);
}

const SEVERITY_ORDER = { P1: 0, P2: 1, P3: 2 };

// Aggregates per-reviewer results into the single verdict the delivery gate reads.
// `mode: 'all'` needs every required reviewer to pass; `mode: 'first_pass'` accepts
// the first required PASS and records the rest as skipped.
export function combineReviews(entries, { mode = 'all', headSha, skipped = [] } = {}) {
  assert(Array.isArray(entries) && entries.length, 'no reviewer results to combine');
  const required = entries.filter(entry => entry.required !== false);
  assert(required.length, 'no required reviewer result to combine');

  for (const entry of entries) {
    const result = entry.result;
    assert(result && ['PASS', 'CHANGES_REQUIRED', 'BLOCKED'].includes(result.verdict), `reviewer ${entry.key} has no valid verdict`);
    if (headSha) assert.equal(result.head_sha ?? entry.head_sha ?? headSha, headSha, `reviewer ${entry.key} reviewed another candidate`);
  }

  const tag = (entry, finding) => ({
    ...finding,
    reviewer: entry.key,
    ...(finding.id ? { id: `${entry.key}/${finding.id}` } : {}),
  });
  const findings = entries.flatMap(entry => (entry.result.findings ?? []).map(finding => tag(entry, finding)))
    .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9));
  const checkKey = check => `${check.command}\u0000${check.status}\u0000${check.exit_code}`;
  const checks = [...new Map(entries.flatMap(entry => entry.result.checks ?? []).map(check => [checkKey(check), check])).values()];
  const limitations = entries.flatMap(entry => (entry.result.limitations ?? []).map(text => `[${entry.key}] ${text}`));
  for (const key of skipped) limitations.push(`[${key}] skipped: an earlier reviewer already returned PASS (mode=first_pass)`);

  const blocked = required.filter(entry => entry.result.verdict === 'BLOCKED');
  const rejected = required.filter(entry => entry.result.verdict === 'CHANGES_REQUIRED');
  const passed = required.filter(entry => entry.result.verdict === 'PASS');

  let verdict;
  if (mode === 'first_pass' && passed.length) verdict = 'PASS';
  else if (blocked.length) verdict = 'BLOCKED';
  else if (rejected.length) verdict = 'CHANGES_REQUIRED';
  else if (passed.length === required.length) verdict = 'PASS';
  else verdict = 'BLOCKED';

  // A PASS cannot carry a blocking finding or a failed check; the gate enforces the
  // same rule, and a combination that violated it is a programming error.
  if (verdict === 'PASS') {
    assert(!findings.some(finding => ['P1', 'P2'].includes(finding.severity)), 'combined PASS carries a blocking finding');
    assert(checks.length && checks.every(check => check.status === 'PASS' && check.exit_code === 0), 'combined PASS carries a failed or missing check');
  }

  const summary = entries.map(entry => `${entry.label || entry.key}: ${entry.result.verdict} — ${firstLine(entry.result.summary)}`).join('；');
  return {
    head_sha: headSha ?? entries[0].result.head_sha,
    verdict,
    summary,
    findings,
    checks,
    limitations,
    reviewers: entries.map(entry => ({ key: entry.key, label: entry.label || entry.key, agent_id: entry.agent_id, verdict: entry.result.verdict, result_path: entry.result_path })),
  };
}

function firstLine(text) {
  const value = String(text ?? '').replace(/\s+/g, ' ').trim();
  return value.length > 160 ? `${value.slice(0, 157)}…` : value;
}

// The next required reviewer without a result for the current head.
export function pendingReviewer(reviewers, results, headSha) {
  const done = new Set((results ?? []).filter(result => result.head_sha === headSha).map(result => result.key));
  return reviewers.find(reviewer => !done.has(reviewer.key)) ?? null;
}
