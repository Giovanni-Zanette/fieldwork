import copy
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import zipfile
from backend.engine import DEFAULT_TEMPLATE, decimal_value, validate_template, validate_result, extract_pages, read_pages, anchor_matches
from backend.storage import Store
from backend.service import Service

ROOT=Path(__file__).resolve().parents[1]

class EngineTests(unittest.TestCase):
    def test_literal_anchor_respects_header_token_boundary(self):
        self.assertTrue(anchor_matches('SKU Description Price','SKU'))
        self.assertFalse(anchor_matches('SKU-123 Wooden tray','SKU'))
        self.assertFalse(anchor_matches('Totals: 5','Total'))
        self.assertTrue(anchor_matches('Total:5','Total:'))
    def result(self):
        return {'fields':{'supplier':'Example','order_number':'A1','order_date':'2026-10-04','subtotal':'0.30','tax':'0.05','total':'0.35'},'items':[{'description':'Item','quantity':'3','unit_price':'0.10','line_total':'0.30'}],'extraction_issues':[],'text_pdf':True}

    def test_exact_decimal_and_fractional_currency_never_rounded_into_valid(self):
        self.assertEqual(validate_result(self.result(),DEFAULT_TEMPLATE)['status'],'validated')
        result=self.result();result['items'][0]['unit_price']='0.104'
        self.assertEqual(validate_result(result,DEFAULT_TEMPLATE)['status'],'needs_review')
        self.assertEqual(result['items'][0]['unit_price'],'0.104')
        self.assertEqual(decimal_value('R 1,250.20'),Decimal('1250.20'))

    def test_invalid_numbers_and_date(self):
        result=self.result();result['fields']['order_date']='2026-02-30';result['fields']['tax']='NaN'
        issues=validate_result(result,DEFAULT_TEMPLATE)['issues']
        self.assertEqual(sum(i['code']=='type' for i in issues),2)

    def test_arithmetic_checks_independent_of_field_types(self):
        result=self.result();result['items'][0]['line_total']='0.31';result['fields']['total']='0.36'
        self.assertTrue({'line_mismatch','subtotal_mismatch','total_mismatch'}<={i['code'] for i in validate_result(result,DEFAULT_TEMPLATE)['issues']})

    def test_nonfinancial_custom_schema(self):
        template={'name':'Contact form','fields':[{'name':'contact','label':'Contact email','type':'email','required':True,'anchor':'Email:','position':'after'}],'table':{'enabled':False,'columns':[]},'validation':{'enabled':False},'duplicate_fields':['contact']}
        validate_template(template)
        result={'fields':{'contact':'hello@example.invalid'},'items':[],'extraction_issues':[]}
        self.assertEqual(validate_result(result,template)['status'],'validated')
        result['fields']['contact']='bad';self.assertEqual(validate_result(result,template)['status'],'needs_review')

    def test_mappings_and_overlapping_columns_rejected(self):
        template=copy.deepcopy(DEFAULT_TEMPLATE);template['validation']['total']='unknown'
        with self.assertRaises(ValueError):validate_template(template)
        template=copy.deepcopy(DEFAULT_TEMPLATE);template['table']['columns'][1]['x0']=.1
        with self.assertRaises(ValueError):validate_template(template)

    def test_actual_text_pdf_and_source_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages=read_pages(ROOT/'samples/purchase-order.pdf',tmp)
            result=extract_pages(pages,DEFAULT_TEMPLATE)
        self.assertEqual(result['status'],'validated');self.assertEqual(len(result['items']),1)
        self.assertTrue(result['evidence']['order_number']['bbox'][0]>=0)

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name);self.service=Service(self.store)
        self.template=self.service.save_template(copy.deepcopy(DEFAULT_TEMPLATE))['id']
    def tearDown(self):
        self.service.close()
        for name in ['worker','watcher']:
            thread=getattr(self.service,name,None)
            if thread:thread.join(3)
        self.tmp.cleanup()
    def import_sample(self):return self.service.import_file((ROOT/'samples/purchase-order.pdf').read_bytes(),'sample.pdf')
    def await_job(self,id):
        for _ in range(100):
            with self.store.connect() as conn:row=conn.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
            if row['status'] in {'completed','failed','cancelled'}:return row
            time.sleep(.05)
        self.fail('Job did not complete')

    def test_versioned_template_snapshot_and_real_job(self):
        id=self.import_sample();job=self.service.queue([id],self.template)[0]
        newer=self.service.template(self.template);newer['name']='Edited';self.service.save_template(newer,self.template)
        self.service.start();result=self.await_job(job)
        self.assertEqual(result['status'],'completed')
        row=self.service.document(id);self.assertEqual(row['template_version'],1)
        self.assertEqual(json.loads(row['template_snapshot'])['name'],'Purchase orders')

    def test_crash_recovery_and_cancellation_persist(self):
        id=self.import_sample();job=self.service.queue([id],self.template)[0]
        with self.store.connect() as conn:conn.execute("UPDATE jobs SET status='running' WHERE id=?",(job,))
        reopened=Store(self.tmp.name)
        with reopened.connect() as conn:self.assertEqual(conn.execute('SELECT status FROM jobs WHERE id=?',(job,)).fetchone()[0],'queued')
        with reopened.connect() as conn:conn.execute("UPDATE jobs SET status='running',cancel_requested=1 WHERE id=?",(job,))
        reopened=Store(self.tmp.name)
        with reopened.connect() as conn:self.assertEqual(conn.execute('SELECT status FROM jobs WHERE id=?',(job,)).fetchone()[0],'cancelled')

    def test_duplicate_hold_and_explicit_keep(self):
        a=self.import_sample();b=self.import_sample();jobs=self.service.queue([a,b],self.template);self.service.start()
        for id in jobs:self.assertEqual(self.await_job(id)['status'],'completed')
        row=self.service.document(a);result=json.loads(row['result_json']);self.assertIn('duplicate',[i['code'] for i in result['issues']])
        with self.store.connect() as conn:self.service.mark_duplicates(conn,a,result,json.loads(row['template_snapshot']),'keep')
        self.assertNotIn('duplicate',[i['code'] for i in result['issues']])

    def test_backup_restore_preserves_sources_and_pauses_watches(self):
        id=self.import_sample()
        with self.store.connect() as conn:conn.execute('INSERT INTO watches VALUES(?,?,?,?,?,?)',('watch',self.tmp.name,self.template,0,None,time.time()))
        data=self.service.backup().read()
        with self.store.connect() as conn:conn.execute('UPDATE documents SET name=? WHERE id=?',('Changed',id))
        previous=self.service.restore(data)
        self.assertTrue((self.store.root/'backups'/previous).is_file());self.assertEqual(self.service.document(id)['name'],'sample.pdf')
        with self.store.connect() as conn:self.assertEqual(conn.execute('SELECT enabled FROM watches').fetchone()[0],0)
        self.assertFalse(any(n.endswith('.key') for n in zipfile.ZipFile(io.BytesIO(data)).namelist()))

    def test_malicious_backup_paths_rejected(self):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('manifest.json',json.dumps({'application':'fieldwork','format':1}));archive.writestr('fieldwork.sqlite',b'bad');archive.writestr('../escape.txt','bad')
        with self.assertRaises(ValueError):self.service.restore(output.getvalue())
        self.assertFalse((self.store.root.parent/'escape.txt').exists())

if __name__=='__main__':unittest.main()
