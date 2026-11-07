import assert from 'node:assert/strict';
import { mkdir, realpath, stat } from 'node:fs/promises';
import path from 'node:path';
import { command } from './local.mjs';

export const candidateSource = config => config.review?.candidate_source ?? 'github';
const objectId = /^[a-f0-9]{40}$/;
// Only full branch refs, never revision expressions, wildcards or command options.
export function checkGitRef(ref) {
  assert(typeof ref === 'string' && /^refs\/heads\/[A-Za-z0-9][A-Za-z0-9/_.-]*$/.test(ref)
    && !ref.includes('..') && !ref.endsWith('.') && ref.split('/').every(part => part && !part.startsWith('.') && !part.endsWith('.lock')),
  'expected a full branch ref such as refs/heads/feature');
  return ref;
}

export function makeGitCandidateIO(config, run = command) {
  const git = (args, cwd = config.repository_path) => run(config.git_path ?? 'git', args, cwd);
  const remote = config.review?.git_remote ?? 'origin';
  const baseRef = config.review?.git_base_ref ?? 'refs/heads/main';
  async function refs(...requested) {
    assert(/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(remote), 'invalid git_remote name');
    requested.forEach(checkGitRef);
    const lines = (await git(['ls-remote', '--heads', remote, ...requested])).trim().split(/\r?\n/).filter(Boolean);
    const result = new Map();
    for (const line of lines) {
      const [sha, ref] = line.split('\t');
      assert(objectId.test(sha) && !result.has(ref), 'invalid remote ref response');
      if (requested.includes(ref)) result.set(ref, sha);
    }
    return result;
  }

  async function liveGit(number, job) {
    const ref = checkGitRef(job.git_ref);
    assert(ref !== baseRef, 'review branch cannot be the base branch');
    const current = await refs(baseRef, ref);
    const base = current.get(baseRef);
    assert(base, 'configured base branch was not found');
    const head = current.get(ref);
    return { number, state: head ? 'open' : 'closed', draft: false, body: null, merged: false,
      head: { sha: head, ref }, base: { sha: base, repo: { full_name: config.repository } } };
  }

  async function prepare(job) {
    const root = config.review?.worktree_root ?? path.join(config.state_dir, 'review-worktrees');
    assert(path.isAbsolute(root), 'review worktree root must be absolute');
    const worktree = job.worktree ?? path.join(root, job.git_ref
      ? `issue-${job.pr_number}-${job.head_sha}-${job.base_sha}` : `pr-${job.pr_number}-${job.head_sha.slice(0, 12)}`);
    if (!job.worktree) {
      await mkdir(root, { recursive: true });
      await git(job.git_ref
        ? ['fetch', '--no-tags', '--no-recurse-submodules', remote, job.git_ref, baseRef]
        : ['fetch', 'origin']);
      const exists = await stat(worktree).then(() => true).catch(error => { if (error.code === 'ENOENT') return false; throw error; });
      if (!exists) await git(['worktree', 'add', '--detach', worktree, job.head_sha]);
    }
    const top = (await git(['rev-parse', '--show-toplevel'], worktree)).trim();
    assert.equal(await realpath(top), await realpath(worktree), 'review directory is not a worktree root');
    assert.equal((await git(['rev-parse', 'HEAD'], worktree)).trim(), job.head_sha, 'review checkout head changed');
    assert.equal((await git(['status', '--porcelain', '--untracked-files=no'], worktree)).trim(), '', 'review checkout has tracked changes');
    if (job.git_ref) {
      assert.equal((await git(['rev-parse', '--verify', job.base_sha + '^{commit}'], worktree)).trim(), job.base_sha, 'review base unavailable');
    }
    return worktree;
  }

  return { git, prepare, refs, liveGit };
}
