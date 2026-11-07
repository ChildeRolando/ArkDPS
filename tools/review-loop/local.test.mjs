import test from 'node:test';
import assert from 'node:assert/strict';
import { commandWithFiles } from './local.mjs';

test('file stdio captures Unicode without mixing stderr into CLI JSON', async () => {
  const body = await commandWithFiles(process.execPath, ['-e', 'process.stderr.write("diagnostic"); process.stdout.write(JSON.stringify({ title: "\\u65e5\\u672c" }));']);
  assert.deepEqual(JSON.parse(body), { title: '\u65e5\u672c' });
});

test('file stdio reports the process exit code and rejects partial stdout', async () => {
  await assert.rejects(commandWithFiles(process.execPath, ['-e', 'process.stdout.write("partial"); process.exit(7)']), { code: 7 });
});
