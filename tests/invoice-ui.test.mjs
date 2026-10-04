import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = readFileSync('static/app.js', 'utf8');
const start = source.indexOf('function validISODate(');
const end = source.indexOf('function markReviewDirty(', start);
assert.ok(start >= 0 && end > start);
const context = {};
runInNewContext(source.split('\n')[1] + '\n' + source.slice(start, end), context);

test('review offers a date picker only for a real ISO calendar date', () => {
  const html = context.inputFor({ type: 'date' }, '2026-10-04', 'data-field="invoice_date"');
  assert.match(html, /type="date"/);
  assert.match(html, /value="2026-10-04"/);
  assert.match(html, /data-field="invoice_date"/);
  assert.equal(context.validISODate('2026-02-31'), false);
  assert.equal(context.validISODate('2024-02-29'), true);
});

test('raw and invalid dates remain visible rather than being cleared by date input', () => {
  for (const raw of ['25 Aug 2026', '03/04/2026', '2026-02-31']) {
    const html = context.inputFor({ type: 'date' }, raw);
    assert.match(html, /type="text"/);
    assert.ok(html.includes(`value="${raw}"`));
    assert.match(html, /original value is kept/);
    assert.match(html, /YYYY-MM-DD/);
  }
});

test('date source text is escaped and an empty date remains editable', () => {
  const html = context.inputFor({ type: 'date' }, '"><script>bad</script>');
  assert.ok(!html.includes('<script>'));
  assert.match(html, /&quot;&gt;&lt;script&gt;/);
  assert.match(context.inputFor({ type: 'date' }, ''), /type="date" value=""/);
});

function starterHarness(discard = true) {
  const invoice = { name: 'Invoice', fields: [{ name: 'invoice_number', label: 'Invoice number', type: 'text', aliases: ['Invoice #'] }], table: { mode: 'header', columns: [] } };
  const old = { id: 'saved-po', name: 'Old purchase order', fields: [{ name: 'order_number' }] };
  const state = { data: { templatePresets: [{ id: 'invoice', name: 'Invoice', description: 'Find headings.', template: invoice }], defaultTemplate: old }, template: old, templateId: old.id, reviewDirty: true };
  const elements = new Map();
  for (const id of ['#template-preset', '#preset-description', '#new-template-name', '#create-template']) elements.set(id, {});
  let dialogs = 0;
  const ctx = { state, structuredClone, clone: structuredClone, invoicePreset: () => state.data.templatePresets[0], allowDiscard: async () => discard,
    $: id => elements.get(id), render() {}, closeDialog() {}, toast() {},
    showDialog(title, html) { dialogs++; assert.equal(title, 'Create a template'); assert.match(html, />Invoice<\/option>/); elements.get('#template-preset').value = 'invoice'; elements.get('#new-template-name').value = 'Supplier invoices'; } };
  const a = source.indexOf('function uniqueTemplateName('), b = source.indexOf('function renderWatches(', a);
  assert.ok(a >= 0 && b > a);
  runInNewContext(source.split('\n')[1] + '\n' + source.slice(a, b), ctx);
  return { ctx, state, invoice, old, elements, get dialogs() { return dialogs; } };
}

test('Invoice creates an independent preset copy and leaves the saved order template intact', async () => {
  const h = starterHarness(), original = JSON.stringify(h.invoice), old = JSON.stringify(h.old);
  await h.ctx.newTemplate('invoice');
  h.elements.get('#create-template').onclick();
  assert.equal(h.state.templateId, null);
  assert.equal(h.state.template.name, 'Supplier invoices');
  assert.equal(h.state.template.fields[0].name, 'invoice_number');
  assert.equal(h.state.template.table.mode, 'header');
  assert.equal(h.state.templateDirty, true);
  h.state.template.fields[0].aliases.push('Custom label');
  assert.equal(JSON.stringify(h.invoice), original);
  assert.equal(JSON.stringify(h.old), old);
});

test('cancelling unsaved-change discard does not open or apply the Invoice starter', async () => {
  const h = starterHarness(false), before = JSON.stringify(h.state);
  await h.ctx.newTemplate('invoice');
  assert.equal(h.dialogs, 0);
  assert.equal(JSON.stringify(h.state), before);
});


test('automatic Invoice suggestion is distinct from existing manual and automatic templates', () => {
  const h = starterHarness();
  h.state.data.templates = [{name:'Invoices'}, {name:'Invoices — automatic'}, {name:'INVOICES — AUTOMATIC (2)'}];
  assert.equal(h.ctx.presetSuggestedName({id:'invoice',name:'Invoice'}), 'Invoices — automatic (3)');
  assert.equal(h.ctx.uniqueTemplateName('Delivery notes'), 'Delivery notes');
  assert.equal(h.state.data.templates[0].name, 'Invoices');
});


test('direct Invoice action opens an editable uniquely named draft without a redundant modal', async () => {
  const h = starterHarness();
  h.state.data.templates = [{name:'Invoices'}, {name:'Invoices — automatic'}];
  await h.ctx.useInvoiceTemplate();
  assert.equal(h.dialogs, 0);
  assert.equal(h.state.template.name, 'Invoices — automatic (2)');
  assert.equal(h.state.templateId, null);
  assert.equal(h.state.templateDirty, true);
  assert.equal(h.state.mode, 'teach');
  assert.equal(h.old.name, 'Old purchase order');
});

test('direct Invoice action preserves unsaved work when discard is rejected', async () => {
  const h = starterHarness(false), before = JSON.stringify(h.state);
  await h.ctx.useInvoiceTemplate();
  assert.equal(JSON.stringify(h.state), before);
});
