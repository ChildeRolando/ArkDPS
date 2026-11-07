import assert from 'node:assert/strict';
import { command } from './local.mjs';

export const uuid = value => typeof value === 'string'
  && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value);
export const projectIds = config => [...new Set([config.project_id, ...(config.project_ids ?? [])].filter(Boolean))];

export function eligibleIssue(config, issue) {
  assert(issue?.workspace_id === config.workspace_id && projectIds(config).includes(issue.project_id),
    'issue outside mapped workspace/projects');
}

export function multicaIO(config, run = command) {
  const call = async args => {
    try {
      return JSON.parse(await run(config.multica_path ?? 'multica',
        [...(config.profile ? ['--profile', config.profile] : []), '--workspace-id', config.workspace_id, ...args, '--output', 'json']));
    } catch (error) {
      if (error.transport) error.message = 'Multica operation failed (' + args.slice(0, 2).join(' ') + '); inspect locally';
      throw error;
    }
  };
  const issue = key => {
    assert(uuid(key) || /^[A-Za-z][A-Za-z0-9]*-[1-9][0-9]*$/.test(key), 'expected a Multica issue UUID or key');
    return call(['issue', 'get', key]);
  };
  return {
    issue, issueByKey: issue,
    runs: id => call(['issue', 'runs', id]),
    runMessages: id => call(['issue', 'run-messages', id]),
    assign: (id, agent) => call(['issue', 'assign', id, '--to-id', agent, '--no-start']),
    rerun: id => call(['issue', 'rerun', id]),
    statusReview: id => call(['issue', 'update', id, '--status', 'in_review', '--no-start']),
    close: id => call(['issue', 'update', id, '--status', 'done', '--no-start']),
    workspace: () => call(['workspace', 'get', config.workspace_id]),
    project: id => call(['project', 'get', id]),
    agent: id => call(['agent', 'get', id]),
  };
}
