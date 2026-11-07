import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { open, readFile, rename, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';

const exec = promisify(execFile);
export async function command(file, args, cwd) {
  try {
    return (await exec(file, args, { cwd, windowsHide: true, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024, timeout: 120000 })).stdout;
  } catch (cause) {
    const error = new Error(`${path.basename(file)} operation failed (${args[0] ?? ''}); inspect locally`, { cause });
    error.exitCode = Number.isInteger(cause.code) ? cause.code : null;
    error.transport = true;
    throw error;
  }
}

export async function atomicJson(file, data) {
  const temp = `${file}.${process.pid}.tmp`;
  await writeFile(temp, JSON.stringify(data, null, 2) + '\n', { mode: 0o600 });
  await rename(temp, file);
}

export async function readJson(file, fallback) {
  try { return JSON.parse(await readFile(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return fallback; throw error; }
}

export async function taskLock(dir, prNumber) {
  const file = path.join(dir, `pr-${prNumber}.lock`);
  let handle;
  try { handle = await open(file, 'wx'); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    // Serialize stale-lock recovery so a second reclaimer cannot unlink a new owner's lock.
    let recovery;
    try { recovery = await open(`${file}.recovery`, 'wx'); }
    catch (retry) { if (retry.code === 'EEXIST') return null; throw retry; }
    try {
      const owner = await readJson(file, null).catch(error => { if (error instanceof SyntaxError) return null; throw error; });
      if (!Number.isSafeInteger(owner?.pid)) return null;
      try { process.kill(owner.pid, 0); return null; }
      catch (probe) { if (probe.code !== 'ESRCH') return null; }
      await unlink(file).catch(error => { if (error.code !== 'ENOENT') throw error; });
      try { handle = await open(file, 'wx'); }
      catch (retry) { if (retry.code === 'EEXIST') return null; throw retry; }
    } finally { await recovery.close(); await unlink(`${file}.recovery`); }
  }
  await handle.writeFile(JSON.stringify({ pid: process.pid }));
  return async () => { await handle.close(); await unlink(file); };
}
