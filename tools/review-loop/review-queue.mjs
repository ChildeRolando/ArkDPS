import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { command, atomicJson, taskLock } from './local.mjs';
import { deliver, makeDeliveryIO } from './delivery.mjs';
import { eligibleIssue, multicaIO } from './multica.mjs';
import { loadConfig, checkConfig } from './config.mjs';
import { candidateSource, makeGitCandidateIO, checkGitRef } from './git-candidate.mjs';
import { MULTI_ENGINE, combineReviews, pendingReviewer, resolveReviewers, reviewerArtifact, reviewerCapacityKeys, reviewerSnapshot, selectPriorityReviewer } from './reviewers.mjs';

const directory = config => path.join(config.state_dir, candidateSource(config) === 'git' ? 'git-review-queue' : 'review-queue');
const queueFile = (config, number) => path.join(directory(config), `pr-${number}.json`);
const belongs = (config, job) => job?.repository === config.repository && job.workspace_id === config.workspace_id
  && Boolean(job.git_ref) === (candidateSource(config) === 'git');
const live = (io, job) => job.git_ref ? io.liveGit(job.pr_number, job) : io.live(job.pr_number);
const current = (job, pr) => pr.state === 'open' && pr.head.sha === job.head_sha
  && (!job.git_ref || pr.base.sha === job.base_sha);
const contractMatches = (job, issue) => !job.git_ref || (job.issue.project_id === issue.project_id
  && job.issue.title === issue.title && job.issue.description === issue.description);

const legacyResultsDir = (config, prNumber) => path.join(directory(config), 'results', `pr-${prNumber}`);
function reviewResultsDir(config, job) {
  return job.result_directory ?? (job.result_path ? path.dirname(job.result_path) : legacyResultsDir(config, job.pr_number));
}
function validateResultRoots(config) {
  for (const key of ['result_root', 'result_workspace_root']) if (config.review?.[key] != null) {
    assert(typeof config.review[key] === 'string' && path.isAbsolute(config.review[key]), `review.${key} must be absolute`);
  }
}
const sha = value => typeof value === 'string' && /^[a-f0-9]{40}$/.test(value);
const active = new Set(['queued', 'dispatched', 'running', 'waiting_local_directory']);
const finished = new Set(['PASSED', 'DONE', 'NEEDS_ATTENTION', 'CANDIDATE_CHANGED', 'CLOSED_UNMERGED']);
const hasDelivery = config => candidateSource(config) === 'github' && config.delivery?.enabled === true;
const resumableNative = job => ['multica', MULTI_ENGINE].includes(job.engine) && job.status === 'NEEDS_ATTENTION'
  && job.native?.stage === 'review' && (job.error?.includes(' operation failed (')
    || !job.error && job.last_review?.verdict === 'CHANGES_REQUIRED'
      && job.reviews?.at(-1)?.head_sha === job.head_sha);
const deliveryStage = status => status === 'DONE' ? 'DONE'
  : finished.has(status) || ['BLOCKED', 'BASE_CHANGED', 'REOPENED'].includes(status) ? 'NEEDS_ATTENTION' : 'DELIVERING';
const currentReviewRuns = (job, runs) => runs.filter(run => run.agent_id === job.native?.agent_id
  && !(job.native?.prior_run_ids ?? []).includes(run.id));
function reviewTimedOut(job, runs, minutes) {
  const running = runs.find(run => run.status === 'running');
  if (!running && runs.some(run => active.has(run.status))) return false;
  return Date.now() - Date.parse(running?.started_at ?? job.native.requested_at) > (minutes ?? 25) * 60000;
}
async function json(file, fallback = null) {
  try { return JSON.parse((await readFile(file, 'utf8')).replace(/^\uFEFF/, '')); }
  catch (error) { if (error.code === 'ENOENT') return fallback; throw error; }
}

export function validateReview(result) {
  assert(['PASS', 'CHANGES_REQUIRED', 'BLOCKED'].includes(result?.verdict) && typeof result.summary === 'string', 'invalid review result');
  assert(Array.isArray(result.findings) && Array.isArray(result.checks) && Array.isArray(result.limitations), 'review evidence is missing');
  const blockers = result.findings.filter(finding => ['P1', 'P2'].includes(finding.severity));
  if (result.verdict === 'PASS') assert(!blockers.length && result.checks.length > 0
    && result.checks.every(check => check.status === 'PASS' && check.exit_code === 0), 'PASS contradicts findings or checks');
  if (result.verdict === 'CHANGES_REQUIRED') assert(blockers.length, 'changes required without a blocking finding');
  return result;
}

export function makeReviewIO(config, run = command) {
  const github = candidateSource(config) === 'github';
  const io = github ? makeDeliveryIO(config, run) : multicaIO(config, run);
  return { ...io, ...makeGitCandidateIO(config, run),
    capacity: async resource => {
      const headers = resource.token_file ? { authorization: `Bearer ${(await readFile(resource.token_file, 'utf8')).trim()}` } : {};
      const response = await fetch(resource.status_url, { headers, signal: AbortSignal.timeout(3000) });
      assert(response.ok, `capacity endpoint HTTP ${response.status}`);
      const value = await response.json();
      assert(value.capacity === 1 && typeof value.busy === 'boolean', 'invalid browser capacity response');
      return value;
    },

  };
}

export async function registerReview(config, prNumber, issueKey, io = makeReviewIO(config), worktree = null, engine = config.review?.engine ?? 'multica') {
  assert(config.review?.enabled === true, 'review queue is disabled');
  assert.equal(candidateSource(config), 'github', 'use register-git for a Git candidate');
  assert(Number.isSafeInteger(prNumber) && prNumber > 0, 'invalid PR number');
  const [pr, issue] = await Promise.all([io.live(prNumber), io.issueByKey(issueKey)]);
  return registerCandidate(config, prNumber, pr, issue, engine, worktree);
}

export async function registerGitReview(config, issueKey, gitRef, io = makeReviewIO(config)) {
  assert(config.review?.enabled === true, 'review queue is disabled');
  assert.equal(candidateSource(config), 'git', 'use register for a GitHub PR');
  checkGitRef(gitRef);
  const issue = await io.issueByKey(issueKey);
  eligibleIssue(config, issue);
  assert(Number.isSafeInteger(issue.number) && issue.number > 0, 'Multica issue number is missing');
  assert(!['done', 'cancelled', 'canceled'].includes(issue.status), 'issue is terminal');
  const pr = await io.liveGit(issue.number, { git_ref: gitRef });
  return registerCandidate(config, issue.number, pr, issue, config.review?.engine ?? 'multica', null, gitRef);
}

async function registerCandidate(config, prNumber, pr, issue, engine, worktree, gitRef) {
  assert(config.review?.enabled === true, 'review queue is disabled');
  validateResultRoots(config);
  assert(['multica', MULTI_ENGINE].includes(engine), 'unknown review engine');
  const multi = engine === MULTI_ENGINE ? resolveReviewers(config) : null;
  eligibleIssue(config, issue);
  assert(pr?.number === prNumber && pr.base?.repo?.full_name === config.repository && sha(pr.head?.sha), 'wrong or incomplete PR');
  assert(pr.state === 'open' && !pr.draft && (!gitRef || sha(pr.base.sha)), 'candidate is not open and ready');
  const dir = directory(config); await mkdir(dir, { recursive: true });
  const unlock = await taskLock(dir, prNumber);
  if (!unlock) return { status: 'ALREADY_RUNNING', pr_number: prNumber };
  try {
    const file = queueFile(config, prNumber), old = await json(file);
    const sameGitCandidate = !gitRef || (old?.git_ref === gitRef && old.base_sha === pr.base.sha && contractMatches(old, issue)
      && old.status !== 'CANDIDATE_CHANGED');
    if (old) {
      assert(belongs(config, old) && old.issue_id === issue.id, 'candidate slot belongs to another issue or workspace');
      if (gitRef && (old.head_sha !== pr.head.sha || !sameGitCandidate)) {
        assert(!['SENDING', 'NATIVE_REVIEWING'].includes(old.status), 'a reviewer is still processing this candidate; reconcile first');
        if (['AWAITING_FIX', 'REPAIR_READY'].includes(old.status)) assert.equal(old.git_ref, gitRef, 'repair must use the original review branch');
      }
    }
    if (old?.head_sha === pr.head.sha && sameGitCandidate) {
      // Switching the reviewer set (or the aggregation mode) for an already
      // registered head must start a fresh review: the old verdict belongs to a
      // different panel and must not be reused as acceptance.
      const samePanel = !multi || (JSON.stringify(reviewerSnapshot(multi)) === JSON.stringify(old.reviewers ?? null)
        && (old.review_mode ?? 'all') === multi.mode);
      if (engine !== old.engine || !samePanel) {
        assert(!['SENDING', 'NATIVE_REVIEWING', 'REPAIR_READY', 'AWAITING_FIX', 'REVIEWING', 'FIXING'].includes(old.status), 'a review or repair is already pending');
        if (gitRef) {
          old.history = [...(old.history ?? []), { event: 'superseded_panel', result_path: old.result_path, review: old.last_review }];
          old.result_directory = path.join(config.review?.result_root ?? path.join(dir, 'results'), 'issue-' + prNumber, randomUUID());
          delete old.result_path;
        }
        old.engine = engine; old.status = 'QUEUED'; old.round = 0; old.reviews = []; delete old.native; delete old.error;
        old.review_results = []; delete old.reviewer_index; delete old.last_review;
        if (multi) { old.reviewers = reviewerSnapshot(multi); old.review_mode = multi.mode; }
        old.history = [...(old.history ?? []), { event: 'reviewer_set_changed', at: new Date().toISOString() }];
        await atomicJson(file, old);
        return { status: old.status, pr_number: prNumber, registered: true, reset: 'reviewer-set' };
      }
      return { status: old.status, pr_number: prNumber, registered: true };
    }
    const repair = old?.status === 'AWAITING_FIX';
    const job = { repository: config.repository, workspace_id: config.workspace_id, pr_number: prNumber, issue_id: issue.id, head_sha: pr.head.sha,
      base_sha: pr.base.sha, branch: pr.head.ref, pr_body: pr.body, issue,
      engine, worktree, status: 'QUEUED', round: repair ? old.round : 0, reviews: repair ? old.reviews : [],
      ...(gitRef ? { git_ref: gitRef, result_directory: path.join(config.review?.result_root ?? path.join(dir, 'results'),
        `issue-${prNumber}`, randomUUID()) } : config.review?.result_root ? { result_directory: path.join(config.review.result_root, `pr-${prNumber}`) } : {}),
      ...(multi ? { reviewers: reviewerSnapshot(multi), review_mode: multi.mode, review_results: [] } : {}),
      ...(repair ? { last_review: old.last_review } : {}),
      history: old ? [...(old.history ?? []), { head_sha: old.head_sha, base_sha: old.base_sha, status: old.status, engine: old.engine, result_path: old.result_path }] : [],
      registered_at: new Date().toISOString() };
    await atomicJson(file, job);
    return { status: 'QUEUED', pr_number: prNumber, issue_id: issue.id };
  } finally { await unlock(); }
}

export async function discoverReviews(config, io = makeReviewIO(config)) {
  if (config.review?.enabled !== true || candidateSource(config) !== 'github') return [];
  const found = [];
  const prefixes = config.review?.issue_prefixes;
  const issueKeys = text => [...new Set(((text ?? '').match(/\b[A-Z][A-Z0-9]*-[1-9][0-9]*\b/g) ?? [])
    .filter(key => !prefixes || prefixes.includes(key.split('-')[0])))];
  for (const pr of await io.openPRs()) {
    if (pr.draft) continue;
    const titleKeys = issueKeys(pr.title);
    const keys = titleKeys.length ? titleKeys : issueKeys(pr.body);
    if (keys.length !== 1) continue; // Multiple possible owners require explicit registration.
    try {
      const issue = await io.issueByKey(keys[0]); eligibleIssue(config, issue);
      if (issue.status !== 'in_review') continue;
      const runs = await io.runs(issue.id);
      if (runs.some(run => active.has(run.status))) continue;
      found.push(await registerReview(config, pr.number, issue.id, io));
    } catch (error) { found.push({ status: 'SKIPPED', pr_number: pr.number, reason: error.message }); }
  }
  return found;
}

export async function runQueuedReview(config, prNumber, io = makeReviewIO(config)) {
  assert(config.review?.enabled === true, 'review queue is disabled');
  const dir = directory(config); await mkdir(dir, { recursive: true });
  const unlock = await taskLock(dir, prNumber);
  if (!unlock) return { status: 'ALREADY_RUNNING', pr_number: prNumber };
  try {
    const file = path.join(dir, `pr-${prNumber}.json`), job = await json(file);
    assert(belongs(config, job) && job.pr_number === prNumber, 'review job missing or belongs to another repository');
    const save = async status => { job.status = status; job.updated_at = new Date().toISOString(); await atomicJson(file, job); };
    if (resumableNative(job) && !job.git_ref) {
      job.history ??= []; job.history.push({ event: 'resume_native', error: job.error ?? 'former round limit', at: new Date().toISOString() });
      const review = job.reviews?.at(-1);
      const consumed = review?.result_path === job.result_path && review?.head_sha === job.head_sha;
      const resume = consumed && !job.error ? 'REPAIR_READY' : 'NATIVE_REVIEWING';
      delete job.error; await save(resume);
    }
    if (finished.has(job.status)) return { status: job.status, pr_number: prNumber };
    if (job.engine === 'multica') return await runMulticaReview(config, job, io, save);
    assert.equal(job.engine, MULTI_ENGINE, 'unknown queued review engine');
    return await runMultiReview(config, job, io, save);
  } finally { await unlock(); }
}

async function startNative(config, job, stage, io, save, runs, reviewer = null) {
  if (job.git_ref) assert(!runs.some(run => active.has(run.status)), 'a Multica run is still active on this issue');
  const agent = stage === 'review' ? (reviewer?.agent_id ?? config.reviewer_id) : config.fixer_id;
  assert(agent && agent !== config.autopilot_id, 'direct review/repair agent is missing');
  job.native = {
    stage,
    agent_id: agent,
    prior_run_ids: runs.map(run => run.id),
    requested_at: new Date().toISOString(),
    ...(reviewer ? { reviewer_key: reviewer.key } : {}),
  };
  await save('SENDING'); // Persist before either platform write. A lost rerun response must not create a second run.
  await sendNative(job, io, save);
}

async function sendNative(job, io, save) {
  await io.assign(job.issue_id, job.native.agent_id);
  job.native.rerun_attempted = true; await save('SENDING');
  try { await io.rerun(job.issue_id); } catch (error) { job.native.error = error.message; }
  await save(job.native.stage === 'review' ? 'NATIVE_REVIEWING' : 'AWAITING_FIX');
}

async function runMulticaReview(config, job, io, save) {
  const result = (status = job.status, reason) => ({ status, pr_number: job.pr_number, engine: 'multica', ...(reason ? { reason } : {}) });
  try {
    const issue = await io.issue(job.issue_id); eligibleIssue(config, issue);
    if (job.git_ref && (!contractMatches(job, issue) || ['done', 'cancelled', 'canceled'].includes(issue.status))) {
      await save('CANDIDATE_CHANGED'); return result(job.status, 'issue acceptance or terminal state changed');
    }
    if (job.status === 'REVIEWED') return await finishReview(config, job, io, save);
    const pr = await live(io, job);
    if (pr.merged && issue.status === 'done') { await save('DONE'); return result(); }
    const runs = await io.runs(job.issue_id);
    if (job.status === 'REPAIR_READY') {
      if (job.git_ref && issue.status !== 'in_review') return result('WAITING_AUTHOR');
      if (!current(job, pr)) { await save('CANDIDATE_CHANGED'); return result(); }
      if (runs.some(run => active.has(run.status))) return result('WAITING_REVIEW');
      await startNative(config, job, 'fix', io, save, runs); return result();
    }
    if (job.status === 'AWAITING_FIX') {
      const repairs = runs.filter(run => run.agent_id === job.native.agent_id && !job.native.prior_run_ids.includes(run.id));
      if (repairs.some(run => active.has(run.status)) || pr.head.sha !== job.head_sha) return result('WAITING_AUTHOR');
      if (!repairs.length) return result('WAITING_DISPATCH');
      await save('NEEDS_ATTENTION'); return result(job.status, 'repair run ended without registering a new candidate');
    }
    if (!current(job, pr)) { await save('CANDIDATE_CHANGED'); return result(); }
    if (job.status === 'QUEUED') {
      if (issue.status !== 'in_review' || runs.some(run => active.has(run.status))) return result('WAITING_AUTHOR');
      job.issue = issue; job.base_sha = pr.base.sha; job.pr_body = pr.body;
      job.worktree = await io.prepare(job);
      if (job.git_ref && !current(job, await live(io, job))) { await save('CANDIDATE_CHANGED'); return result(); }
      job.round++;
      const dir = reviewResultsDir(config, job); await mkdir(dir, { recursive: true });
      job.result_path = path.join(dir, `${job.round}-${job.head_sha}-multica-review.json`);
      await startNative(config, job, 'review', io, save, runs);
      return result();
    }
    if (job.status === 'SENDING') {
      const newRuns = runs.filter(run => run.agent_id === job.native.agent_id && !job.native.prior_run_ids.includes(run.id));
      if (newRuns.length) { await save(job.native.stage === 'review' ? 'NATIVE_REVIEWING' : 'AWAITING_FIX'); return result(); }
      else if (!job.native.rerun_attempted) {
        if (job.git_ref && (issue.status !== 'in_review' || runs.some(run => active.has(run.status)))) return result('WAITING_AUTHOR');
        await sendNative(job, io, save); return result();
      }
      else { await save('NEEDS_ATTENTION'); return result(job.status, 'dispatch interrupted; inspect issue runs before retrying'); }
    }
    const reviews = currentReviewRuns(job, runs);
    if ((!job.git_ref || reviews.length) && await json(job.result_path)) return await acceptMulticaResult(config, job, io, save);
    if (reviews.length && !reviews.some(run => active.has(run.status))) {
      await save('NEEDS_ATTENTION'); return result(job.status, 'review ended without delivering its result file');
    }
    if (reviewTimedOut(job, reviews, config.review.timeout_minutes)) {
      if (reviews.some(run => active.has(run.status))) {
        job.native.waiting_budget_exceeded_at ??= new Date().toISOString(); await save(job.status);
        return result('WAITING_REVIEW', 'waiting budget exceeded; the actual run is still active and retains its slot');
      }
      await save('NEEDS_ATTENTION'); return result(job.status, 'review has not delivered within its waiting budget; no automatic Codex fallback');
    }
    return result(reviews.length ? 'WAITING_REVIEW' : 'WAITING_DISPATCH');
  } catch (error) {
    if (error.transport === true) {
      job.last_io_error = { message: error.message, at: new Date().toISOString(), stage: job.status };
      await save(job.status); return result('RETRY_IO', error.message);
    }
    job.error = error.message; await save('NEEDS_ATTENTION'); return result(job.status, error.message);
  }
}

async function readVerifiedResult(job, io) {
  const reviewed = await json(job.result_path);
  assert.equal(reviewed?.head_sha, job.head_sha, 'review result belongs to another candidate');
  if (job.git_ref) assert.equal(reviewed?.base_sha, job.base_sha, 'review result belongs to another base');
  const review = validateReview(reviewed);
  assert.equal((await io.git(['rev-parse', 'HEAD'], job.worktree)).trim(), job.head_sha, 'review checkout head changed');
  assert.equal((await io.git(['status', '--porcelain', '--untracked-files=no'], job.worktree)).trim(), '', 'Reviewer modified tracked files');
  assert(current(job, await live(io, job)), 'candidate changed before review acceptance');
  return review;
}

async function acceptMulticaResult(config, job, io, save) {
  const review = await readVerifiedResult(job, io);
  job.last_review = review;
  if (!job.reviews.some(item => item.result_path === job.result_path)) job.reviews.push({ head_sha: job.head_sha, result: review, result_path: job.result_path, engine: 'multica' });
  await save('REVIEWED');
  return finishReview(config, job, io, save);
}

async function finishReview(config, job, io, save) {
  const review = job.last_review;
  const result = extra => ({ status: job.status, pr_number: job.pr_number, engine: job.engine, ...extra });
  if (job.git_ref && !current(job, await live(io, job))) {
    await save('CANDIDATE_CHANGED'); return result();
  }
  if (hasDelivery(config)) {
    const url = job.reported_result_path === job.result_path && job.review_url
      ? job.review_url : await io.report(job, job.result_path);
    job.review_url = url; job.reported_result_path = job.result_path; await save('REVIEWED');
  }
  if (review.verdict === 'PASS') {
    if (!hasDelivery(config)) {
      await save('PASSED'); return result({ review, result_path: job.result_path });
    }
    job.delivery_request = { pr_number: job.pr_number, head_sha: job.head_sha, issue_id: job.issue_id,
      acceptance: { outcome: 'review_passed', summary: review.summary, reference: job.result_path, url: job.review_url,
        checks: review.checks, findings: review.findings, limitations: review.limitations,
        reviewer: review.reviewers?.map(entry => entry.label).join(' + ') || 'Multica', reviewers: review.reviewers } };
    await save('DELIVERING'); const delivery = await deliver(config, job.delivery_request, io);
    await save(deliveryStage(delivery.status)); return result({ delivery });
  }
  if (!job.git_ref) await io.statusReview(job.issue_id);
  await save(review.verdict === 'CHANGES_REQUIRED' ? 'REPAIR_READY' : 'NEEDS_ATTENTION');
  return result({ review_url: job.review_url });
}

// --- multi-reviewer engine ---------------------------------------------------
// Every reviewer is a Multica agent, so the queue does not care which runtime
// backs it (Claude, Codex, or the ChatGPT-Web shim). Reviewers run one at a time;
// their verdicts are combined before the existing delivery gate sees them.
async function dispatchNextReviewer(config, job, io, save, runs) {
  const pending = pendingReviewer(job.reviewers ?? [], job.review_results ?? [], job.head_sha);
  assert(pending, 'no pending reviewer for this candidate');
  const candidates = job.review_mode === 'priority' ? job.reviewers : [pending];
  const reviewer = selectPriorityReviewer(candidates, await activeReviewCounts(config, job, runs, io), config);
  if (!reviewer) { await save('WAITING_CAPACITY'); return null; }
  const dir = reviewResultsDir(config, job); await mkdir(dir, { recursive: true });
  job.result_path = reviewerArtifact(dir, job.round, job.head_sha, reviewer.key);
  await startNative(config, job, 'review', io, save, runs, reviewer);
  return reviewer;
}

// Conversation text diagnoses transport only. The result file alone carries the verdict.
async function recordTextAvailability(job, io, reviews) {
  const pattern = /text_unavailable|无法加载此回复|unable\s+to\s+load\s+this\s+response/i;
  for (const run of reviews.filter(run => ['failed', 'cancelled'].includes(run.status))) {
    let texts = [run.error, run.error_message];
    if (typeof io?.runMessages === 'function') {
      try { const messages = await io.runMessages(run.id); texts.push(...(Array.isArray(messages) ? messages : [])
        .filter(message => message.type === 'error' || /text_unavailable:/.test(String(message.content ?? ''))).map(message => message.content)); } catch {}
    }
    if (texts.some(text => pattern.test(String(text ?? '')))) job.native.channel_state = 'text_unavailable';
  }
}

// Slots held by reviews currently dispatched or in flight, across every PR of
// this queue. The current job retains a slot only while its actual run is active.
async function activeReviewCounts(config, currentJob, currentRuns, io) {
  const counts = {};
  let files = [];
  try { files = (await readdir(directory(config))).filter(name => /^pr-[1-9][0-9]*\.json$/.test(name)); } catch { return counts; }
  for (const name of files) {
    let job;
    try { job = await json(path.join(directory(config), name)); } catch { continue; }
    if (!belongs(config, job) || job.engine !== MULTI_ENGINE) continue;
    const current = job.pr_number === currentJob.pr_number;
    if (current) job = currentJob;
    if (job.native?.stage !== 'review' || !job.native.reviewer_key) continue;
    let occupied = current ? currentReviewRuns(job, currentRuns).some(run => active.has(run.status))
      : ['SENDING', 'NATIVE_REVIEWING'].includes(job.status);
    if (!occupied && !current) {
      try { occupied = currentReviewRuns(job, await io.runs(job.issue_id)).some(run => active.has(run.status)); }
      catch { occupied = true; } // An unreadable run is not proof that its worker stopped.
    }
    const reviewer = (job.reviewers ?? []).find(item => item.key === job.native.reviewer_key);
    const keys = reviewerCapacityKeys({ key: job.native.reviewer_key, agent_id: job.native.agent_id, ...reviewer }, config);
    if (keys.length > 1 && (occupied || !finished.has(job.status))) {
      for (const key of keys) counts[key] = Infinity; // An unresolved group migration reserves both groups.
    } else if (occupied) for (const key of keys) counts[key] = (counts[key] ?? 0) + 1;
  }
  for (const [key, resource] of Object.entries(config.review?.capacity_resources ?? {})) {
    if (!resource.status_url) continue;
    let occupied = true;
    try { occupied = (await io.capacity(resource)).busy; } catch { /* unavailable capacity stays reserved */ }
    if (occupied) counts[key] = Math.max(counts[key] ?? 0, resource.max_concurrency ?? 1);
  }
  return counts;
}

async function runMultiReview(config, job, io, save) {
  const result = (status = job.status, reason) => ({ status, pr_number: job.pr_number, engine: MULTI_ENGINE, ...(reason ? { reason } : {}) });
  try {
    const issue = await io.issue(job.issue_id); eligibleIssue(config, issue);
    if (job.git_ref && (!contractMatches(job, issue) || ['done', 'cancelled', 'canceled'].includes(issue.status))) {
      await save('CANDIDATE_CHANGED'); return result(job.status, 'issue acceptance or terminal state changed');
    }
    if (job.status === 'REVIEWED') return await finishReview(config, job, io, save);
    const pr = await live(io, job);
    if (pr.merged && issue.status === 'done') { await save('DONE'); return result(); }
    const runs = await io.runs(job.issue_id);

    if (job.status === 'REPAIR_READY') {
      if (job.git_ref && issue.status !== 'in_review') return result('WAITING_AUTHOR');
      if (!current(job, pr)) { await save('CANDIDATE_CHANGED'); return result(); }
      if (runs.some(run => active.has(run.status))) return result('WAITING_REVIEW');
      await startNative(config, job, 'fix', io, save, runs); return result();
    }
    if (job.status === 'AWAITING_FIX') {
      const repairs = runs.filter(run => run.agent_id === job.native.agent_id && !job.native.prior_run_ids.includes(run.id));
      if (repairs.some(run => active.has(run.status)) || pr.head.sha !== job.head_sha) return result('WAITING_AUTHOR');
      if (!repairs.length) return result('WAITING_DISPATCH');
      await save('NEEDS_ATTENTION'); return result(job.status, 'repair run ended without registering a new candidate');
    }
    if (!current(job, pr)) { await save('CANDIDATE_CHANGED'); return result(); }

    if (job.status === 'QUEUED' || job.status === 'WAITING_CAPACITY') {
      const ownRuns = job.status === 'WAITING_CAPACITY' ? currentReviewRuns(job, runs) : [];
      if (issue.status !== 'in_review' || runs.some(run => active.has(run.status) && !ownRuns.includes(run))) return result('WAITING_AUTHOR');
      if (job.status === 'QUEUED') {
        job.issue = issue; job.base_sha = pr.base.sha; job.pr_body = pr.body;
        job.worktree = await io.prepare(job);
      if (job.git_ref && !current(job, await live(io, job))) { await save('CANDIDATE_CHANGED'); return result(); }
        job.round++;
        job.review_results = (job.review_results ?? []).filter(entry => entry.head_sha !== job.head_sha);
      }
      const reviewer = await dispatchNextReviewer(config, job, io, save, runs);
      if (!reviewer) return result('WAITING_CAPACITY', 'review capacity is occupied or its mapping changed; restore the mapping or drain pending tasks');
      return result('WAITING_REVIEW', `dispatched reviewer ${reviewer.key}`);
    }

    if (job.status === 'SENDING') {
      const newRuns = runs.filter(run => run.agent_id === job.native.agent_id && !job.native.prior_run_ids.includes(run.id));
      if (newRuns.length) { await save(job.native.stage === 'review' ? 'NATIVE_REVIEWING' : 'AWAITING_FIX'); return result(); }
      if (!job.native.rerun_attempted) {
        if (job.git_ref && (issue.status !== 'in_review' || runs.some(run => active.has(run.status)))) return result('WAITING_AUTHOR');
        if (job.native.stage === 'review') {
          const reviewer = job.reviewers.find(item => item.key === job.native.reviewer_key);
          assert(reviewer, 'pending dispatch reviewer is missing');
          if (!selectPriorityReviewer([reviewer], await activeReviewCounts(config, job, runs, io), config)) {
            return result('WAITING_CAPACITY', 'pending review capacity is occupied or its mapping changed; restore the mapping or drain pending tasks');
          }
        }
        await sendNative(job, io, save); return result();
      }
      await save('NEEDS_ATTENTION'); return result(job.status, 'dispatch interrupted; inspect issue runs before retrying');
    }

    if (job.status === 'NATIVE_REVIEWING') {
      const reviews = runs.filter(run => run.agent_id === job.native.agent_id && !job.native.prior_run_ids.includes(run.id));
      if ((!job.git_ref || reviews.length) && await json(job.result_path)) {
        await recordTextAvailability(job, null, reviews);
        return await acceptReviewerResult(config, job, io, save, runs);
      }
      // A platform write can fail after the ledger was moved to NATIVE_REVIEWING
      // (flaky network). Retry the dispatch a bounded number of times instead of
      // leaving the job waiting for a run that was never created.
      if (!job.git_ref && job.native?.error && !reviews.length) {
        const attempts = (job.native.dispatch_retries ?? 0) + 1;
        if (attempts <= 3) {
          job.native = { ...job.native, error: null, rerun_attempted: false, dispatch_retries: attempts };
          await save('SENDING');
          return result('WAITING_DISPATCH', `retrying reviewer ${job.native.reviewer_key} dispatch (${attempts}/3) after a platform error`);
        }
      }
      if (reviews.length && !reviews.some(run => active.has(run.status))) {
        await recordTextAvailability(job, io, reviews);
        await save('NEEDS_ATTENTION');
        return result(job.status, `${job.native.channel_state === 'text_unavailable' ? 'text_unavailable: ' : ''}reviewer ${job.native.reviewer_key} ended without delivering its result file`);
      }
      if (reviewTimedOut(job, reviews, config.review.timeout_minutes)) {
        if (reviews.some(run => active.has(run.status))) {
          job.native.waiting_budget_exceeded_at ??= new Date().toISOString(); await save(job.status);
          return result('WAITING_REVIEW', 'waiting budget exceeded; the actual run is still active and retains its slot');
        }
        await save('NEEDS_ATTENTION');
        return result(job.status, `reviewer ${job.native.reviewer_key} has not delivered within its waiting budget`);
      }
      return result(reviews.length ? 'WAITING_REVIEW' : 'WAITING_DISPATCH');
    }
    return result();
  } catch (error) {
    if (error.transport === true) {
      job.last_io_error = { message: error.message, at: new Date().toISOString(), stage: job.status };
      await save(job.status); return result('RETRY_IO', error.message);
    }
    job.error = error.message; await save('NEEDS_ATTENTION'); return result(job.status, error.message);
  }
}

async function acceptReviewerResult(config, job, io, save, runs) {
  const review = await readVerifiedResult(job, io);

  const key = job.native.reviewer_key;
  const label = (job.reviewers ?? []).find(reviewer => reviewer.key === key)?.label ?? key;
  const required = (job.reviewers ?? []).find(reviewer => reviewer.key === key)?.required !== false;
  const entry = { head_sha: job.head_sha, key, label, required, agent_id: job.native.agent_id, result: review, result_path: job.result_path };
  job.review_results = [...(job.review_results ?? []).filter(item => !(item.head_sha === job.head_sha && item.key === key)), entry];
  if (!job.reviews.some(item => item.result_path === job.result_path)) job.reviews.push({ head_sha: job.head_sha, reviewer: key, result: review, result_path: job.result_path, engine: MULTI_ENGINE });
  await save(job.status);

  const mode = job.review_mode ?? 'all';
  if (mode === 'priority') return await finalizeMultiReview(config, job, io, save);
  const headResults = job.review_results.filter(item => item.head_sha === job.head_sha);
  const firstPass = mode === 'first_pass' && headResults.some(item => item.required && item.result.verdict === 'PASS');
  if (!firstPass) {
    const next = pendingReviewer(job.reviewers ?? [], job.review_results, job.head_sha);
    if (next) {
      if (job.git_ref && runs.some(run => active.has(run.status))) {
        return { status: 'WAITING_REVIEW', pr_number: job.pr_number, engine: MULTI_ENGINE };
      }
      const reviewer = await dispatchNextReviewer(config, job, io, save, runs);
      return { status: reviewer ? 'WAITING_REVIEW' : 'WAITING_CAPACITY', pr_number: job.pr_number, engine: MULTI_ENGINE, reviewer: next.key };
    }
  }
  return await finalizeMultiReview(config, job, io, save);
}

async function finalizeMultiReview(config, job, io, save) {
  const mode = job.review_mode ?? 'all';
  const entries = job.review_results.filter(item => item.head_sha === job.head_sha);
  assert(entries.length, 'no reviewer result for this candidate');
  let review, combinedPath;
  if (mode === 'priority') {
    // The selected reviewer's verdict is the round's verdict; nothing is merged.
    const entry = entries.at(-1);
    review = { ...entry.result,
      reviewers: [{ key: entry.key, label: entry.label, agent_id: entry.agent_id, verdict: entry.result.verdict, result_path: entry.result_path }] };
    combinedPath = entry.result_path;
  } else {
    const skipped = (job.reviewers ?? [])
      .filter(reviewer => reviewer.required && !entries.some(entry => entry.key === reviewer.key))
      .map(reviewer => reviewer.key);
    review = combineReviews(entries, { mode, headSha: job.head_sha, skipped });
    const dir = reviewResultsDir(config, job); await mkdir(dir, { recursive: true });
    combinedPath = reviewerArtifact(dir, job.round, job.head_sha, 'combined');
    await atomicJson(combinedPath, review);
  }
  job.last_review = review; job.result_path = combinedPath;
  await save('REVIEWED');
  return finishReview(config, job, io, save);
}

// Re-queue a stopped review or a native preparation with no dispatch evidence.
// A repair, delivery or unknown dispatch must not be reset behind the operator's
// back. The fixed head, reviewer panel and caller directory are retained.
export async function retryReview(config, prNumber, io = makeReviewIO(config)) {
  assert(config.review?.enabled === true, 'review queue is disabled');
  const dir = directory(config); await mkdir(dir, { recursive: true });
  const unlock = await taskLock(dir, prNumber);
  if (!unlock) return { status: 'ALREADY_RUNNING', pr_number: prNumber };
  try {
    return await retryLocked(config, prNumber, io);
  } finally { await unlock(); }
}

async function retryLocked(config, prNumber, io) {
  const file = queueFile(config, prNumber);
  const job = await json(file);
  assert(job, `no review job for PR #${prNumber}`);
  assert(belongs(config, job), 'review job belongs to another repository');
  if (job.status === 'QUEUED') return { status: 'QUEUED', pr_number: prNumber, already: true };
  assert(job.status === 'NEEDS_ATTENTION', `retry needs NEEDS_ATTENTION, job is ${job.status}`);
  const priorResults = [...(job.reviews ?? []), ...(job.review_results ?? [])];
  const undispatched = ['multica', MULTI_ENGINE].includes(job.engine) && job.native == null
    && ['result_path', 'review_url', 'reported_result_path', 'delivery_request', 'last_repair', 'reviewer_index']
      .every(key => job[key] == null)
    && priorResults.every(entry => sha(entry.head_sha) && entry.head_sha !== job.head_sha)
    && (!job.last_review || sha(job.last_review.head_sha) && job.last_review.head_sha !== job.head_sha)
    && (!job.last_io_error || ['QUEUED', 'WAITING_CAPACITY'].includes(job.last_io_error.stage));
  assert(job.native?.stage === 'review' || undispatched,
    'retry only re-queues a failed review or an undispatched native preparation, not a repair, delivery or unknown dispatch');
  const runs = await io.runs(job.issue_id);
  if (undispatched) assert(Array.isArray(runs) && runs.every(run => ['completed', 'failed', 'cancelled'].includes(run.status)),
    'issue run is active or unknown; preparation retry requires every actual run to have ended');
  else assert(!currentReviewRuns(job, runs).some(run => active.has(run.status)), 'review run is still active; retain its capacity and context');
  if (job.git_ref && job.native?.rerun_attempted) assert(currentReviewRuns(job, runs).length > 0,
    'dispatch outcome is unknown; verify the Multica run before retrying');

  job.history = [...(job.history ?? []), {
    event: 'retry', at: new Date().toISOString(), previous: job.status,
    reviewer: job.native?.reviewer_key ?? null, error: job.error ?? null,
    worktree: job.worktree ?? null, ...(undispatched ? { stage: 'prepare' } : {}),
  }];
  job.retry_count = (job.retry_count ?? 0) + 1;
  job.status = 'QUEUED';
  delete job.native; delete job.error; delete job.last_io_error;
  if (!undispatched) delete job.last_review;
  delete job.review_url; delete job.reported_result_path; delete job.delivery_request;
  job.review_results = (job.review_results ?? []).filter(entry => entry.head_sha !== job.head_sha);
  await atomicJson(file, job);
  return { status: 'QUEUED', pr_number: prNumber, retries: job.retry_count, head_sha: job.head_sha };
}

export async function reviewContext(config, issueKey, io = makeReviewIO(config), actor = {}) {
  const issue = await io.issueByKey(issueKey); eligibleIssue(config, issue);
  const jobs = [];
  for (const file of await readdir(directory(config))) if (/^pr-[1-9][0-9]*\.json$/.test(file)) {
    let job;
    try { job = await json(path.join(directory(config), file)); } catch { continue; }
    if (belongs(config, job) && job.issue_id === issue.id && ['multica', MULTI_ENGINE].includes(job.engine)
      && (!finished.has(job.status) || job.status === 'NEEDS_ATTENTION' && job.native?.stage === 'review')) jobs.push(job);
  }
  assert.equal(jobs.length, 1, 'no unique direct-review task for this issue');
  const job = jobs[0];
  if (job.git_ref) {
    assert(job.native && contractMatches(job, issue) && !['done', 'cancelled', 'canceled'].includes(issue.status),
      'no current dispatch for this issue and acceptance');
    if (job.native.stage === 'review') assert(current(job, await live(io, job)), 'candidate changed before review context');
  }
  if (actor.agent_id) assert.equal(job.native?.agent_id, actor.agent_id, 'context belongs to another reviewer');
  if (actor.run_id) assert(currentReviewRuns(job, await io.runs(job.issue_id)).some(run => run.id === actor.run_id), 'context belongs to another run');
  let connectorPaths = {};
  if (actor.workspace_root) {
    assert(path.isAbsolute(actor.workspace_root), 'connector workspace root must be absolute');
    const relative = path.relative(actor.workspace_root, job.worktree);
    assert(relative && relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative), 'review worktree is outside the connector workspace');
    connectorPaths = { connector_workspace_root: actor.workspace_root, worktree_relative_path: relative };
  }
  const reviewer = job.native?.reviewer_key
    ? (job.reviewers ?? []).find(item => item.key === job.native.reviewer_key) ?? { key: job.native.reviewer_key }
    : null;
  validateResultRoots(config);
  const resultRoot = config.review?.result_workspace_root ?? config.review?.result_root ?? config.runtime_root ?? config.state_dir;
  const resultRelative = path.relative(resultRoot, job.result_path);
  assert(resultRelative && resultRelative !== '..' && !resultRelative.startsWith('..' + path.sep) && !path.isAbsolute(resultRelative), 'result file is outside its configured workspace');
  return { stage: job.native?.stage ?? 'review', repository: job.repository,
    ...(job.git_ref ? { candidate_source: 'git', issue_number: job.pr_number, git_ref: job.git_ref } : { candidate_source: 'github', pr_number: job.pr_number }), issue,
    head_sha: job.head_sha, base_sha: job.base_sha, branch: job.branch, worktree: job.worktree, result_path: job.result_path, ...connectorPaths,
    result_workspace_root: resultRoot, result_relative_path: resultRelative,
    ...(job.engine === MULTI_ENGINE ? {
      reviewer: reviewer ? { key: reviewer.key, label: reviewer.label ?? reviewer.key } : null,
      reviewers: (job.reviewers ?? []).map(item => ({ key: item.key, label: item.label, required: item.required })),
      review_mode: job.review_mode ?? 'all',
    } : {}),
    previous_review: job.last_review ?? null, previous_head_sha: job.reviews.at(-1)?.head_sha ?? null,
    result_submission: 'Write the final result file atomically. The intermediary owns all state transitions and delivery.' };
}

export async function reconcileReviews(config, io = makeReviewIO(config)) {
  if (config.review?.enabled !== true) return { status: 'DISABLED', tasks: [] };
  const dir = directory(config); await mkdir(dir, { recursive: true });
  const unlock = await taskLock(dir, 0); // Serialize scans; native workers run outside this process.
  if (!unlock) return { status: 'ALREADY_RUNNING', tasks: [] };
  try {
    let discovered;
    try { discovered = await discoverReviews(config, io); }
    catch (error) { discovered = [{ status: 'RETRY_IO', reason: error.message }]; }
    const tasks = [];
    for (const name of (await readdir(dir)).filter(name => /^pr-[1-9][0-9]*\.json$/.test(name))) {
      let job;
      try { job = await json(path.join(dir, name)); assert(belongs(config, job), 'queue record belongs to another repository or workspace'); }
      catch (error) { tasks.push({ status: 'NEEDS_ATTENTION', file: name, reason: error.message }); continue; }
      if (finished.has(job.status) && (job.git_ref || !resumableNative(job))) continue;
      if (job.status === 'DELIVERING') {
        if (!hasDelivery(config)) { tasks.push({ status: 'DELIVERY_DISABLED', pr_number: job.pr_number }); continue; }
        try {
          const result = await deliver(config, job.delivery_request, io);
          job.status = deliveryStage(result.status); await atomicJson(path.join(dir, name), job);
          tasks.push(result);
        } catch (error) { job.status = 'NEEDS_ATTENTION'; job.error = error.message; await atomicJson(path.join(dir, name), job); tasks.push({ status: job.status, pr_number: job.pr_number, reason: error.message }); }
        continue;
      }
      const task = await runQueuedReview(config, job.pr_number, io); tasks.push(task);
    }
    const result = { status: 'OK', discovered, tasks, at: new Date().toISOString() };
    await atomicJson(path.join(dir, 'last-run.json'), result); return result;
  } finally { await unlock(); }
}

async function slotFor(config, target, io) {
  if (candidateSource(config) === 'github') {
    assert(/^[1-9][0-9]*$/.test(target ?? '') && Number.isSafeInteger(Number(target)), 'expected a PR number');
    return Number(target);
  }
  const issue = await io.issueByKey(target);
  eligibleIssue(config, issue);
  assert(Number.isSafeInteger(issue.number) && issue.number > 0, 'Multica issue number is missing');
  return issue.number;
}

async function reviewStatus(config, number) {
  const job = await json(queueFile(config, number));
  if (!job) return { status: 'NOT_REGISTERED', pr_number: number };
  assert(belongs(config, job), 'queue record belongs to another repository or workspace');
  return { status: job.status, pr_number: number, issue_id: job.issue_id, git_ref: job.git_ref,
    head_sha: job.head_sha, base_sha: job.base_sha, round: job.round,
    review: job.last_review, result_path: job.result_path, review_url: job.review_url, error: job.error };
}

export async function runReviewCli(args = process.argv.slice(2)) {
  if (args[0] === 'complete') throw new Error('complete has been removed; Reviewer submits the result file and the intermediary processes it');
  const [action, configPath, target, extra, worktree] = args;
  const usage = 'usage: check|reconcile <config> | register-git <config> <issue> <refs/heads/branch> | register <config> <PR> <issue> | context <config> <issue> | status|run|retry <config> <issue-or-PR>';
  assert(['check', 'register-git', 'register', 'context', 'run', 'retry', 'status', 'reconcile'].includes(action), usage);
  const config = await loadConfig(configPath);
  const io = makeReviewIO(config);
  let result;
  if (action === 'check') result = await checkConfig(config);
  else if (action === 'register') result = await registerReview(config, Number(target), extra, io, worktree ?? null);
  else if (action === 'register-git') result = await registerGitReview(config, target, extra, io);
  else if (action === 'context') {
    const option = name => { const i = args.indexOf(name, 3); return i >= 0 ? args[i + 1] : undefined; };
    result = await reviewContext(config, target, io, { agent_id: option('--agent-id'), run_id: option('--run-id'), workspace_root: option('--workspace-root') });
  }
  else if (action === 'reconcile') result = await reconcileReviews(config, io);
  else {
    const number = await slotFor(config, target, io);
    if (action === 'run') result = await runQueuedReview(config, number, io);
    else if (action === 'retry') result = await retryReview(config, number, io);
    else result = await reviewStatus(config, number);
  }
  const output = JSON.stringify(result, (key, value) => {
    if (candidateSource(config) !== 'git' || !value || typeof value !== 'object' || !('pr_number' in value)) return value;
    const { pr_number, ...rest } = value;
    return { ...rest, issue_number: pr_number };
  });
  process.stdout.write(output + '\n'); return result;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runReviewCli().catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
}
