import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { File } from 'node:buffer';

// Exercise the shipped import function and picker handler, not a duplicate
// implementation. The source override lets us prove the old release fails.
const source = readFileSync(resolve(process.env.FIELDWORK_IMPORT_SOURCE || 'static/app.js'), 'utf8');
const importStart = source.indexOf('async function importFiles(');
const importEnd = source.indexOf('async function loadExamples(', importStart);
const pickerStart = source.indexOf("$('#file-picker').onchange=");
const pickerEnd = source.indexOf("$('#template-picker').onchange=", pickerStart);
assert.ok(importStart >= 0 && importEnd > importStart && pickerStart >= 0 && pickerEnd > pickerStart);
const script = source.slice(importStart, importEnd) + '\n' + source.slice(pickerStart, pickerEnd) + '\nthis.importFiles = importFiles;';
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup() {
  let resolveDiscard;
  const discard = new Promise(resolve => { resolveDiscard = resolve; });
  const uploads = [], messages = [];
  let discardCalls = 0;
  const live = [];
  // File inputs expose a live FileList; resetting the picker empties that same
  // object. DataTransfer files likewise stop being available after dispatch.
  const fileList = { get length() { return live.length; }, *[Symbol.iterator]() { yield* live; } };
  const picker = {
    files: fileList, resets: 0,
    set value(value) { assert.equal(value, ''); this.resets++; live.length = 0; },
  };
  const context = {
    $: selector => { assert.equal(selector, '#file-picker'); return picker; },
    state: { selected: null, checked: new Set(), view: 'documents', reviewDirty: true },
    allowDiscard: () => { discardCalls++; return discard; },
    action: async fn => fn(),
    request: async (path, form) => {
      assert.equal(path, '/api/upload');
      uploads.push(form.getAll('files'));
      return { ids: ['document-' + uploads.length] };
    },
    refresh: async () => {},
    toast: (...args) => messages.push(args),
    FormData,
  };
  runInNewContext(script, context);
  return { context, picker, live, fileList, uploads, messages,
    resolveDiscard, get discardCalls() { return discardCalls; } };
}

const pdf = () => new File(['%PDF-1.7\nsynthetic selection'], 'Test.PDF', { type: 'application/pdf' });
const png = () => new File(['synthetic PNG bytes'], 'scan.png', { type: 'image/png' });

test('picker reset during deferred discard retains selected PDF bytes', async () => {
  const h = setup(), file = pdf();
  h.live.push(file);
  h.picker.onchange({ target: h.picker });
  assert.equal(h.fileList.length, 0);
  assert.equal(h.picker.resets, 1);
  assert.equal(h.uploads.length, 0, 'approval must resolve before uploading');
  h.resolveDiscard(true);
  await flush();
  assert.equal(h.uploads.length, 1);
  assert.equal(h.uploads[0][0].name, 'Test.PDF');
  assert.equal(await h.uploads[0][0].text(), await file.text());
  assert.equal(h.messages.some(([m]) => m.startsWith('Choose PDF')), false);
});

test('drop FileList emptied after event dispatch retains every selected file', async () => {
  const h = setup(), files = [pdf(), png()];
  h.live.push(...files);
  const pending = h.context.importFiles(h.fileList);
  h.live.length = 0;
  h.resolveDiscard(true);
  await pending;
  assert.equal(h.uploads.length, 1);
  assert.deepEqual(h.uploads[0].map(file => file.name), files.map(file => file.name));
  assert.equal(await h.uploads[0][1].text(), await files[1].text());
});

test('rejecting discard never uploads the captured selection', async () => {
  const h = setup(); h.live.push(pdf());
  h.picker.onchange({ target: h.picker });
  h.resolveDiscard(false);
  await flush();
  assert.equal(h.uploads.length, 0);
  assert.equal(h.messages.length, 0);
});

test('empty picker cancellation is silent and does not prompt to discard', async () => {
  const h = setup();
  const pending = h.context.importFiles(h.fileList);
  h.resolveDiscard(true);
  await pending;
  assert.equal(h.discardCalls, 0);
  assert.equal(h.uploads.length, 0);
  assert.equal(h.messages.length, 0);
});

test('unsupported extensions remain rejected even if MIME claims PDF', async () => {
  const h = setup();
  h.resolveDiscard(true);
  await h.context.importFiles([new File(['not a PDF'], 'notes.txt', { type: 'application/pdf' })]);
  assert.equal(h.uploads.length, 0);
  assert.equal(h.messages[0][0], 'Choose PDF, PNG, JPEG or TIFF files.');
});

test('picker reset permits selecting the same file twice', async () => {
  const h = setup(), file = pdf(); h.resolveDiscard(true);
  for (let i = 0; i < 2; i++) {
    h.live.push(file); h.picker.onchange({ target: h.picker });
    await flush();
  }
  assert.equal(h.picker.resets, 2);
  assert.equal(h.uploads.length, 2);
  assert.deepEqual(h.uploads.map(files => files[0].name), ['Test.PDF', 'Test.PDF']);
});
