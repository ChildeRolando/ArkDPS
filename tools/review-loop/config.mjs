import assert from 'node:assert/strict';
import { readFile, realpath } from 'node:fs/promises';
import path from 'node:path';
import { candidateSource, checkGitRef, makeGitCandidateIO } from './git-candidate.mjs';
import { multicaIO, projectIds, uuid } from './multica.mjs';
import { MULTI_ENGINE, resolveReviewers } from './reviewers.mjs';
import { command } from './local.mjs';

export async function loadConfig(file) {
  assert(file, 'a config file is required');
  const config = JSON.parse((await readFile(file, 'utf8')).replace(/^\uFEFF/, ''));
  const root = path.dirname(path.resolve(file));
  const location = (value, key) => {
    assert(typeof value === 'string' && value.trim(), key + ' is required');
    return path.resolve(root, value);
  };
  config.repository_path = location(config.repository_path, 'repository_path');
  config.state_dir = location(config.state_dir, 'state_dir');
  assert(config.state_dir !== config.repository_path, 'state_dir cannot be the repository root');
  assert(typeof config.repository === 'string' && /^[\w.-]+(?:\/[\w.-]+)?$/.test(config.repository), 'invalid repository name');
  assert(uuid(config.workspace_id), 'workspace_id must be a UUID');
  assert(config.project_ids == null || Array.isArray(config.project_ids), 'project_ids must be a list');
  const ids = projectIds(config);
  assert(ids.length > 0 && ids.every(uuid), 'at least one Multica project UUID is required');
  assert(config.review && typeof config.review.enabled === 'boolean', 'set review.enabled explicitly');
  for (const key of ['worktree_root', 'result_root', 'result_workspace_root']) {
    if (config.review[key] != null) config.review[key] = location(config.review[key], 'review.' + key);
  }
  assert(['git', 'github'].includes(candidateSource(config)), 'review.candidate_source must be git or github');
  if (candidateSource(config) === 'git') {
    assert(config.delivery?.enabled !== true, 'GitHub delivery requires the GitHub candidate source');
    checkGitRef(config.review.git_base_ref ?? 'refs/heads/main');
  } else {
    assert(config.repository.includes('/'), 'GitHub requires an owner/name repository');
    const prefixes = config.review.issue_prefixes;
    assert(prefixes == null || (Array.isArray(prefixes) && prefixes.length > 0 && prefixes.every(p => /^[A-Z][A-Z0-9]*$/.test(p))), 'invalid review.issue_prefixes');
  }
  assert(uuid(config.fixer_id), 'fixer_id must be a Multica agent UUID');
  const engine = config.review.engine ?? 'multica';
  assert(['multica', MULTI_ENGINE].includes(engine), 'only Multica review engines are supported');
  for (const id of reviewerIds(config)) {
    assert(uuid(id) && id !== config.fixer_id, 'reviewers must be separate Multica agents with UUIDs');
  }
  return config;
}

const reviewerIds = config => config.review.engine === MULTI_ENGINE
  ? resolveReviewers(config).reviewers.map(reviewer => reviewer.agent_id) : [config.reviewer_id];

export async function checkConfig(config, run = command) {
  const io = multicaIO(config, run);
  const git = makeGitCandidateIO(config, run);
  // Use the same authenticated CLI profile one request at a time during setup.
  const workspace = await io.workspace();
  assert.equal(workspace.id, config.workspace_id, 'Multica returned another workspace');
  const projects = [], agents = [];
  for (const id of projectIds(config)) {
    const project = await io.project(id);
    assert(project.workspace_id === workspace.id && project.id === id, 'a configured project belongs to another workspace');
    projects.push(project);
  }
  for (const id of new Set([config.fixer_id, ...reviewerIds(config)])) {
    const agent = await io.agent(id);
    assert(agent.workspace_id === workspace.id && agent.id === id, 'a configured agent belongs to another workspace');
    agents.push(agent);
  }
  const top = await git.git(['rev-parse', '--show-toplevel']);
  assert.equal(await realpath(top.trim()), await realpath(config.repository_path), 'repository_path must be the Git checkout root');
  let base;
  if (candidateSource(config) === 'git') {
    const ref = config.review.git_base_ref ?? 'refs/heads/main';
    base = (await git.refs(ref)).get(ref);
    assert(base, 'configured base branch was not found');
  } else {
    const repo = JSON.parse(await run(config.gh_path ?? 'gh', ['api', 'repos/' + config.repository]));
    assert.equal(repo.full_name, config.repository, 'GitHub returned another repository');
    const origin = (await git.git(['remote', 'get-url', 'origin'])).trim();
    const matches = ['https://github.com/', 'git@github.com:', 'ssh://git@github.com/']
      .some(prefix => origin.toLowerCase().replace(/\.git$/, '') === (prefix + config.repository).toLowerCase());
    assert(matches, 'Git origin does not match the GitHub repository');
  }
  return { status: 'READY_TO_CONFIGURE', enabled: config.review.enabled, candidate_source: candidateSource(config),
    delivery_enabled: config.delivery?.enabled === true, workspace: { id: workspace.id, name: workspace.name },
    projects: projects.map(({ id, title }) => ({ id, title })), agents: agents.map(({ id, name }) => ({ id, name })), base_sha: base };
}
