import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, open, readFile, rename, rmdir, stat, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

const exec = promisify(execFile);
const maxBuffer = 32 * 1024 * 1024;
const timeout = 120000;

// Windows restricted tokens can reject Node's anonymous stdio pipes while
// accepting ordinary file handles inherited by the same child.
export async function commandWithFiles(file, args, cwd) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'review-loop-stdio-')).catch(error => {
    if (!['EPERM', 'EACCES'].includes(error.code)) throw error;
    return mkdtemp(path.join(process.cwd(), '.review-loop-stdio-'));
  });
  const outPath = path.join(root, 'stdout'), errPath = path.join(root, 'stderr');
  let out, err;
  try {
    out = await open(outPath, 'wx', 0o600);
    err = await open(errPath, 'wx', 0o600);
    const child = spawn(file, args, { cwd, windowsHide: true, stdio: ['ignore', out.fd, err.fd] });
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; child.kill(); }, timeout);
    let code;
    try {
      code = await new Promise((resolve, reject) => {
        child.once('error', reject);
        child.once('close', resolve);
      });
    } finally { clearTimeout(timer); }
    await out.close(); out = null;
    await err.close(); err = null;
    if (timedOut || code !== 0) {
      throw Object.assign(new Error(timedOut ? 'command timed out' : 'command exited with failure'), { code });
    }
    if ((await stat(outPath)).size > maxBuffer) throw new Error('command stdout exceeded limit');
    return await readFile(outPath, 'utf8');
  } finally {
    await out?.close();
    await err?.close();
    for (const name of [outPath, errPath]) await unlink(name).catch(error => { if (error.code !== 'ENOENT') throw error; });
    await rmdir(root);
  }
}

export async function command(file, args, cwd) {
  try {
    try {
      return (await exec(file, args, { cwd, windowsHide: true, encoding: 'utf8', maxBuffer, timeout })).stdout;
    } catch (cause) {
      // A rejected spawn never started the command. Do not retry an attempted
      // process (including a timed-out or uncertain Multica write).
      if (process.platform === 'win32' && cause.code === 'EPERM' && cause.syscall === 'spawn') {
        return await commandWithFiles(file, args, cwd);
      }
      throw cause;
    }
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
