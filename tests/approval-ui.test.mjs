import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = readFileSync('static/app.js', 'utf8');
const start = source.indexOf('function approvalBlockers(');
const end = source.indexOf('function showDuplicate(', start);
assert.ok(start >= 0 && end > start);

function harness(overrides = {}, requestError = null) {
  const document = {
    id: 'review-doc', status: 'needs_review', reviewRevision: 4,
    templateSnapshot: { table: { enabled: true } },
    result: { fields: { invoice_number: 'EXAMPLE-1' }, items: [{ description: 'Example' }], issues: [] },
    ...overrides,
  };
  const calls = { dialogs: [], requests: [], toasts: [], duplicates: 0, refreshes: 0, closes: 0, renders: 0, invoice: 0 };
  const elements = new Map();
  const context = {
    state: { data: { templates: [] }, template: { table: { enabled: false } } },
    doc: () => document,
    $: selector => elements.get(selector),
    invoicePreset: () => ({ id: 'invoice' }),
    showDialog(title, html) {
      calls.dialogs.push({ title, html });
      elements.clear();
      for (const [, id] of html.matchAll(/id="([^"]+)"/g)) {
        elements.set(`#${id}`, { value: '', addEventListener(event, callback) { this[event] = callback; } });
      }
    },
    showDuplicate: () => { calls.duplicates++; },
    closeDialog: () => { calls.closes++; },
    refresh: async () => { calls.refreshes++; },
    renderInspector: () => { calls.renders++; },
    toast: (...args) => calls.toasts.push(args),
    action: callback => callback(),
    request: async (...args) => { calls.requests.push(args); if (requestError) throw requestError; },
    useInvoiceTemplate: async () => { calls.invoice++; },
    editDocumentTemplate() {}, archiveIds() {},
  };
  const templateFor = source.split('\n').find(line => line.startsWith('const templateFor='));
  runInNewContext(source.split('\n')[1] + '\n' + templateFor + '\n' + source.slice(start, end), context);
  return { context, document, calls, elements };
}

test('a result with no expected rows offers recovery, never an override reason', async () => {
  const h = harness({ result: { fields: { supplier: 'Example supplier' }, items: [], issues: [{ code: 'empty_table', message: 'No rows found.' }] } });
  await h.context.approveCurrent();
  assert.equal(h.calls.dialogs.length, 1);
  assert.equal(h.calls.dialogs[0].title, 'Fix this result before approving');
  assert.match(h.calls.dialogs[0].html, /none were extracted/);
  assert.match(h.calls.dialogs[0].html, /Use invoice template/);
  assert.doesNotMatch(h.calls.dialogs[0].html, /approval-note|confirm-approval/);
  assert.equal(h.calls.requests.length, 0);
  h.elements.get('#approval-invoice-template').click();
  assert.equal(h.calls.invoice, 1);
  assert.equal(h.calls.closes, 1);
});

test('blank headers cannot be overridden even when rows are present', async () => {
  const h = harness({ result: { fields: { number: '  ', date: null }, items: [{ description: 'Example' }], issues: [] } });
  await h.context.approveCurrent();
  assert.match(h.calls.dialogs[0].html, /No field values are available/);
  assert.doesNotMatch(h.calls.dialogs[0].html, /approval-note/);
  assert.equal(h.calls.requests.length, 0);
});

test('a header-only template can approve without rows, and numeric zero is a value', async () => {
  const h = harness({ templateSnapshot: { table: { enabled: false } }, result: { fields: { count: 0 }, items: [], issues: [] } });
  await h.context.approveCurrent();
  assert.equal(h.calls.dialogs.length, 0);
  assert.equal(h.calls.requests.length, 1);
  assert.equal(h.calls.requests[0][1].revision, 4);
  assert.equal(h.calls.requests[0][1].override, undefined);
  assert.equal(h.calls.refreshes, 1);
});

test('the saved template governs approval, not the unsaved editor selection', async () => {
  const h = harness();
  h.document.result.items = [];
  assert.equal(h.context.state.template.table.enabled, false);
  await h.context.approveCurrent();
  assert.match(h.calls.dialogs[0].html, /expects line items/);
  assert.equal(h.calls.requests.length, 0);
});

test('real arithmetic and OCR exceptions retain the explicit reason gate', async () => {
  for (const code of ['line_total_mismatch', 'ocr_review', 'required']) {
    const h = harness();
    h.document.result.issues = [{ code, message: 'Check this source value.' }];
    await h.context.approveCurrent();
    assert.equal(h.calls.dialogs[0].title, 'Approve with unresolved issues?');
    assert.match(h.calls.dialogs[0].html, /approval-note/);
    assert.equal(h.calls.requests.length, 0);
    h.elements.get('#approval-note').value = 'short';
    await h.elements.get('#confirm-approval').onclick();
    assert.equal(h.calls.requests.length, 0);
    h.elements.get('#approval-note').value = 'Checked the original and confirmed the value.';
    await h.elements.get('#confirm-approval').onclick();
    assert.equal(h.calls.requests.length, 1);
    assert.equal(h.calls.requests[0][1].override, true);
  }
});

test('a current duplicate issue requires resolution even after an earlier Keep both', async () => {
  const h = harness({ duplicateDecision: 'keep' });
  h.document.result.issues = [{ code: 'duplicate', message: 'A new matching document needs a decision.' }];
  await h.context.approveCurrent();
  assert.equal(h.calls.duplicates, 1);
  assert.equal(h.calls.dialogs.length, 0);
  assert.equal(h.calls.requests.length, 0);
});

test('an ignored copy offers duplicate resolution rather than an approval override', async () => {
  const h = harness({ duplicateDecision: 'ignore' });
  await h.context.approveCurrent();
  assert.match(h.calls.dialogs[0].html, /ignored and excluded from export/);
  assert.doesNotMatch(h.calls.dialogs[0].html, /approval-note/);
  h.elements.get('#approval-resolve-duplicate').click();
  assert.equal(h.calls.duplicates, 1);
  assert.equal(h.calls.requests.length, 0);
});

test('archived, processing and unprocessed documents cannot enter the reason flow', async () => {
  for (const overrides of [{ archived: true }, { status: 'queued' }, { status: 'processing' }, { result: null }]) {
    const h = harness(overrides);
    await h.context.approveCurrent();
    assert.equal(h.calls.dialogs[0].title, 'Fix this result before approving');
    assert.doesNotMatch(h.calls.dialogs[0].html, /approval-note/);
    assert.equal(h.calls.requests.length, 0);
  }
});

test('a stale or newly blocked approval refreshes once without recursively prompting again', async () => {
  for (const issues of [undefined, [{ code: 'duplicate', message: 'A new duplicate exists.' }], [{ code: 'ocr_review', message: 'Review the original.' }]]) {
    const error = Object.assign(new Error('Review the updated document before approving.'), { status: 409, issues });
    const h = harness({}, error);
    const result = await h.context.submitApproval({ revision: 4, override: true, note: 'Previously checked against the source.' });
    assert.equal(result, false);
    assert.equal(h.calls.requests.length, 1);
    assert.equal(h.calls.refreshes, 1);
    assert.equal(h.calls.closes, 1);
    assert.equal(h.calls.dialogs.length, 0);
    assert.equal(h.calls.duplicates, 0);
    assert.equal(h.calls.toasts[0][0], error.message);
    assert.equal(h.calls.toasts[0][1], true);
    if (issues) assert.equal(h.document.result.issues, issues);
  }
});
