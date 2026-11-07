// Existing acceptance -> commit status -> normal merge -> issue closeout.
// No LLM, admission fence, rule receipt, worktree, or legacy ledger is needed.
import assert from 'node:assert/strict';
import { mkdir, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { command, atomicJson, taskLock, readJson } from './local.mjs';
import { multicaIO, projectIds } from './multica.mjs';

const sha = value => typeof value === 'string' && /^[a-f0-9]{40}$/.test(value);
const nonempty = value => typeof value === 'string' && value.trim().length > 0;
const terminal = new Set(['DONE', 'CLOSED_UNMERGED', 'CANDIDATE_CHANGED', 'BASE_CHANGED', 'REOPENED', 'BLOCKED', 'NEEDS_ATTENTION']);
const repositoryPattern = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const canonical = value => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
const evidenceUrl = (config, request) => request.acceptance.url ?? `https://github.com/${config.repository}/pull/${request.pr_number}`;
const description = request => request.acceptance.outcome === 'owner_accepted'
  ? '维护者接受当前候选；原发现保留' : '复用已有验收；仅覆盖所引用的验收范围';
const statusMatches = (config, request, status) => status?.state === 'success'
  && status.target_url === evidenceUrl(config, request) && status.description === description(request);

function validateConfig(config) {
  assert(repositoryPattern.test(config.repository ?? ''), 'repository must be owner/name');
  assert(path.isAbsolute(config.state_dir ?? ''), 'state_dir must be absolute');
  assert(['merge', 'squash', 'rebase'].includes(config.delivery?.merge_method ?? 'squash'), 'invalid merge method');
  assert(Number.isSafeInteger(config.delivery?.max_retry_attempts ?? 10) && (config.delivery?.max_retry_attempts ?? 10) > 0,
    'max_retry_attempts must be a positive integer');
}

export function deliveryRequest(config, request) {
  validateConfig(config);
  assert(request && typeof request === 'object', 'delivery request must be an object');
  assert(request.repository == null || request.repository === config.repository, 'request repository differs from configuration');
  assert(Number.isSafeInteger(request?.pr_number) && request.pr_number > 0, 'invalid PR number');
  assert(sha(request.head_sha), 'acceptance must identify the full reviewed head SHA');
  assert(request.base_sha == null || sha(request.base_sha), 'invalid reviewed base SHA');
  assert(request.issue_id == null || /^[a-f0-9-]{36}$/i.test(request.issue_id), 'invalid issue id');
  assert(['review_passed', 'owner_accepted'].includes(request.acceptance?.outcome), 'acceptance outcome is required');
  assert(nonempty(request.acceptance.summary) && nonempty(request.acceptance.reference), 'acceptance summary and source reference are required');
  const method = request.merge_method ?? config.delivery?.merge_method ?? 'squash';
  assert(['merge', 'squash', 'rebase'].includes(method), 'invalid merge method');
  if (request.acceptance.url != null) assert(new URL(request.acceptance.url).protocol === 'https:', 'evidence URL must use HTTPS');
  if (request.acceptance.checks != null) {
    assert(Array.isArray(request.acceptance.checks), 'checks must be an array');
    for (const check of request.acceptance.checks) {
      assert(nonempty(check.command) && ['PASS', 'FAIL', 'NOT_RUN'].includes(check.status), 'invalid check');
      assert(check.status !== 'PASS' || check.exit_code === 0, 'passing check must have exit code 0');
      assert(check.status !== 'FAIL' || Number.isInteger(check.exit_code) && check.exit_code !== 0, 'failing check must have nonzero exit code');
      assert(check.status !== 'NOT_RUN' || check.exit_code === null, 'unrun check must have null exit code');
      assert(request.acceptance.outcome === 'owner_accepted' || check.status === 'PASS', 'failed or unrun checks require explicit owner acceptance');
    }
  }
  // Extra source metadata is retained; prose layout and provider session IDs are irrelevant.
  return { repository: config.repository, pr_number: request.pr_number, head_sha: request.head_sha, base_sha: request.base_sha ?? null,
    issue_id: request.issue_id ?? null, merge_method: method, acceptance: structuredClone(request.acceptance) };
}

function candidate(config, request, pr) {
  assert(pr?.number === request.pr_number && pr.base?.repo?.full_name === config.repository
    && sha(pr.head?.sha), 'GitHub returned a different or incomplete PR');
  assert(['open', 'closed'].includes(pr.state), 'unknown PR state');
  if (pr.head.sha !== request.head_sha) return 'CANDIDATE_CHANGED';
  if (request.base_sha && pr.base.sha !== request.base_sha) return 'BASE_CHANGED';
  if (pr.state === 'closed') return pr.merged === true ? 'MERGED' : 'CLOSED_UNMERGED';
  return pr.draft === true ? 'DRAFT' : 'OPEN';
}

function issueMatches(config, request, issue) {
  assert(issue?.id === request.issue_id && issue.workspace_id === config.workspace_id
    && projectIds(config).includes(issue.project_id), 'associated issue is outside the configured projects');
  assert(!['cancelled', 'canceled'].includes(issue.status), 'associated issue was cancelled; no automatic closeout');
}

export function makeDeliveryIO(config, run = command) {
  const api = `repos/${config.repository}`;
  const gh = async args => JSON.parse(await run(config.gh_path ?? 'gh', ['api', ...args]));
  return {
    ...multicaIO(config, run),
    live: number => gh([`${api}/pulls/${number}`]),
    sharedHead: async (number, head) => {
      const pages = await gh([`${api}/pulls?state=open&per_page=100`, '--paginate', '--slurp']);
      assert(Array.isArray(pages) && pages.every(Array.isArray), 'open PR readback incomplete');
      return pages.flat().filter(pr => pr.number !== number && pr.head?.sha === head).map(pr => pr.number);
    },
    status: async head => {
      const pages = await gh([`${api}/commits/${head}/statuses?per_page=100`, '--paginate', '--slurp']);
      assert(Array.isArray(pages) && pages.every(Array.isArray), 'status readback incomplete');
      return pages.flat().find(value => value.context === (config.delivery?.status_context ?? 'Review Loop v2')) ?? null;
    },
    publish: request => gh([`${api}/statuses/${request.head_sha}`, '-f', 'state=success',
      '-f', `context=${config.delivery?.status_context ?? 'Review Loop v2'}`,
      '-f', `description=${description(request)}`, '-f', `target_url=${evidenceUrl(config, request)}`]),
    merge: request => run(config.gh_path ?? 'gh', ['pr', 'merge', String(request.pr_number), '--repo', config.repository,
      '--auto', '--match-head-commit', request.head_sha, `--${request.merge_method}`]),
    // Publish evidence only on GitHub; it does not wake Multica agents.
    report: async (job, resultPath) => {
      const reportFile = resultPath + '.md';
      const result = job.last_review;
      const label = job.engine === 'multi' ? 'Multi-reviewer' : 'Multica';
      const markdown = `${label} review · head ${job.head_sha}\n\n${result.summary}\n\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\`\n`;
      await writeFile(reportFile, markdown);
      // A lost response must not publish the same review twice.
      const pages = JSON.parse(await run(config.gh_path ?? 'gh', ['api', `repos/${config.repository}/issues/${job.pr_number}/comments?per_page=100`, '--paginate', '--slurp']));
      assert(Array.isArray(pages) && pages.every(Array.isArray), 'PR review comment readback incomplete');
      const body = markdown.trimEnd();
      const prior = pages.flat().find(comment => comment.body?.replaceAll('\r\n', '\n').trimEnd() === body.replaceAll('\r\n', '\n'));
      if (prior) return prior.html_url;
      return (await run(config.gh_path ?? 'gh', ['pr', 'comment', String(job.pr_number), '--repo', config.repository, '--body-file', reportFile])).trim();
    },
    openPRs: async () => {
      const pages = await gh([`${api}/pulls?state=open&per_page=100`, '--paginate', '--slurp']);
      assert(Array.isArray(pages) && pages.every(Array.isArray), 'open PR readback incomplete');
      return pages.flat();
    },
  };
}

export async function previewDelivery(config, input, io = makeDeliveryIO(config)) {
  const request = deliveryRequest(config, input);
  const pr = await io.live(request.pr_number), state = candidate(config, request, pr);
  if (request.issue_id) issueMatches(config, request, await io.issue(request.issue_id));
  const shared = state === 'OPEN' ? await io.sharedHead(request.pr_number, request.head_sha) : [];
  return { status: shared.length ? 'SHARED_HEAD' : state, request, shared_prs: shared,
    actions: state === 'OPEN' && !shared.length ? ['publish_existing_acceptance', 'normal_merge', ...(request.issue_id ? ['close_after_merge'] : [])]
      : state === 'MERGED' && request.issue_id ? ['close_after_merge'] : [] };
}

export async function deliver(config, input, io = makeDeliveryIO(config)) {
  const request = deliveryRequest(config, input);
  assert(config.delivery?.enabled === true, 'lightweight delivery is disabled');
  const dir = path.join(config.state_dir, 'delivery');
  await mkdir(dir, { recursive: true });
  const unlock = await taskLock(dir, request.pr_number);
  if (!unlock) return { status: 'ALREADY_RUNNING', pr_number: request.pr_number };
  try {
    const file = path.join(dir, `pr-${request.pr_number}.json`);
    let state = await readJson(file, null);
    if (state && canonical(state.request) !== canonical(request)) {
      const old = await io.live(request.pr_number);
      // A new candidate starts a new delivery; an unfinished write to the same candidate must first reconcile.
      assert((state.request.head_sha !== request.head_sha || terminal.has(state.status)) && old.head?.sha === request.head_sha,
        'this candidate already has a different delivery request; reconcile it first');
      state = { request, history: [...(state.history ?? []), { request: state.request, status: state.status }], steps: {} };
    }
    state ??= { request, history: [], steps: {} };
    state.steps ??= {}; state.history ??= [];
    if (state.status === 'NEEDS_ATTENTION') state.retry_failures = 0; // An explicit submit resumes the task.
    const save = async (status, reason = null) => {
      state.status = status; state.reason = reason; state.updated_at = new Date().toISOString();
      if (status === 'RETRY_IO' || status === 'WAITING_MERGE' && state.steps.merge?.error) {
        state.retry_failures = (state.retry_failures ?? 0) + 1;
        if (state.retry_failures >= (config.delivery?.max_retry_attempts ?? 10)) state.status = status = 'NEEDS_ATTENTION';
      }
      await atomicJson(file, state);
      return { status, pr_number: request.pr_number, issue_id: request.issue_id, reason };
    };
    const live = async () => candidate(config, request, await io.live(request.pr_number));
    const closeout = async () => {
      if (!request.issue_id) return save('DONE');
      const issue = await io.issue(request.issue_id); issueMatches(config, request, issue);
      if (issue.status !== 'done') {
        if (await live() !== 'MERGED') return save('CANDIDATE_CHANGED', 'PR changed before issue closeout');
        state.steps.close = { attempted_at: new Date().toISOString() }; await save('CLOSING');
        try { await io.close(request.issue_id); } catch { /* Resolve lost response by reading the actual issue. */ }
        const observed = await io.issue(request.issue_id); issueMatches(config, request, observed);
        if (observed.status !== 'done') return save('RETRY_IO', 'issue is not yet done');
      }
      state.steps.close = { confirmed_at: new Date().toISOString() };
      return save('DONE');
    };
    try {
      let current = await live();
      if (current === 'MERGED') return await closeout();
      if (current !== 'OPEN') return await save(current);
      if (['DONE', 'CLOSED_UNMERGED', 'REOPENED'].includes(state.status)) return await save('REOPENED', 'submit a new acceptance for the reopened PR');
      if (request.issue_id) issueMatches(config, request, await io.issue(request.issue_id));
      const shared = await io.sharedHead(request.pr_number, request.head_sha);
      if (shared.length) return await save('SHARED_HEAD', `commit status is shared with PR ${shared.join(', ')}`);
      // Own confirmed success can be reused. An unrelated green status cannot replace this acceptance.
      const observedStatus = await io.status(request.head_sha);
      if (!(state.steps.publish && statusMatches(config, request, observedStatus))) {
        current = await live();
        if (current === 'MERGED') return await closeout();
        if (current !== 'OPEN') return await save(current);
        state.steps.publish = { attempted_at: new Date().toISOString() }; await save('PUBLISHING');
        try { await io.publish(request); } catch { /* A POST can succeed while its response is lost. */ }
        const observed = await io.status(request.head_sha);
        if (!statusMatches(config, request, observed)) return await save('RETRY_IO', 'acceptance status not yet confirmed');
        state.steps.publish.confirmed_at = new Date().toISOString(); await save('ACCEPTED');
      }
      current = await live();
      if (current === 'MERGED') return await closeout();
      if (current !== 'OPEN') return await save(current);
      const pr = await io.live(request.pr_number);
      current = candidate(config, request, pr);
      if (current === 'MERGED') return await closeout();
      if (current !== 'OPEN') return await save(current);
      if (pr.auto_merge == null) {
        state.steps.merge = { attempted_at: new Date().toISOString() }; await save('MERGING');
        try { await io.merge(request); }
        catch (error) { state.steps.merge.error = error.message; }
      } else if (state.steps.merge) {
        delete state.steps.merge.error;
      }
      current = await live();
      if (current === 'MERGED') return await closeout();
      if (current !== 'OPEN') return await save(current);
      return await save('WAITING_MERGE', state.steps.merge?.error ?? 'GitHub checks or merge conditions are pending');
    } catch (error) {
      return await save(error.name === 'AssertionError' ? 'BLOCKED' : 'RETRY_IO', error.message);
    }
  } finally { await unlock(); }
}

export async function reconcileDeliveries(config, io = makeDeliveryIO(config)) {
  validateConfig(config);
  if (config.delivery?.enabled !== true) return { status: 'DISABLED', tasks: [] };
  const dir = path.join(config.state_dir, 'delivery');
  const names = await readdir(dir).catch(error => { if (error.code === 'ENOENT') return []; throw error; });
  const tasks = [];
  for (const name of names.filter(name => /^pr-[1-9][0-9]*\.json$/.test(name))) {
    try {
      const state = await readJson(path.join(dir, name));
      if (terminal.has(state.status)) continue;
      tasks.push(await deliver(config, state.request, io));
    } catch (error) { tasks.push({ status: 'BLOCKED', file: name, reason: error.message }); }
  }
  return { status: 'OK', tasks };
}
