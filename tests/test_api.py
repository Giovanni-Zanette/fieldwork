import copy
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

BOOT=tempfile.TemporaryDirectory(prefix='fieldwork-api-tests-')
os.environ['FIELDWORK_DATA_DIR']=BOOT.name
os.environ['FIELDWORK_PORT']='4341'
server=importlib.import_module('app')

class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();server.DATA=Path(self.tmp.name);server.store=server.Store(server.DATA);server.service=server.Service(server.store)
        self.client=server.app.test_client();self.base={'base_url':'http://localhost:4341'};self.auth={'Authorization':'Bearer '+server.API_KEY}
    def tearDown(self):
        server.service.close()
        for name in ['worker','watcher']:
            thread=getattr(server.service,name,None)
            if thread:thread.join(3)
        self.tmp.cleanup()
    def post(self,path,payload):return self.client.post(path,json=payload,headers=self.auth,**self.base)
    def prepared(self):
        template=self.post('/api/templates',{'template':copy.deepcopy(server.DEFAULT_TEMPLATE)}).get_json()['id']
        id=self.post('/api/samples',{'name':'purchase-order.pdf'}).get_json()['id']
        self.post('/api/jobs',{'templateId':template,'documentIds':[id]});server.service.start()
        for _ in range(100):
            row=server.service.document(id)
            if row['status'] in {'validated','needs_review','failed'}:return id,template
            time.sleep(.02)
        self.fail('Queue did not finish')
    def get_document(self,id):return self.client.get('/api/documents/'+id,headers=self.auth,**self.base).get_json()

    def test_auth_origin_host_and_cookie_csrf(self):
        self.assertEqual(self.client.get('/api/state',**self.base).status_code,401)
        self.assertEqual(self.client.get('/api/health',**self.base).status_code,200)
        self.assertEqual(self.client.get('/api/state',base_url='http://evil.example:4341',headers=self.auth).status_code,403)
        self.assertEqual(self.client.get('/api/state',headers={**self.auth,'Origin':'https://evil.example'},**self.base).status_code,403)
        response=self.client.post('/api/session',json={'token':server.API_KEY},**self.base)
        self.assertIn('HttpOnly',response.headers['Set-Cookie']);self.assertIn('SameSite=Strict',response.headers['Set-Cookie'])
        self.assertEqual(self.client.post('/api/settings',json={'onboardingDone':True},**self.base).status_code,403)
        self.assertEqual(self.client.post('/api/settings',json={'onboardingDone':True},headers={'X-CSRF-Token':server.CSRF},**self.base).status_code,200)

    def test_template_versions_and_initial_source_labels(self):
        response=self.post('/api/templates',{'template':copy.deepcopy(server.DEFAULT_TEMPLATE)}).get_json()
        updated=copy.deepcopy(server.DEFAULT_TEMPLATE);updated['name']='New name'
        next_version=self.post('/api/templates',{'id':response['id'],'template':updated}).get_json()
        self.assertEqual(next_version['version'],2)
        id=self.post('/api/samples',{'name':'purchase-order.pdf'}).get_json()['id']
        self.assertTrue(self.get_document(id)['pages'][0]['lines'])

    def test_multipart_pdf_and_image_upload(self):
        from PIL import Image
        image=io.BytesIO();Image.new('RGB',(40,40),'white').save(image,format='PNG');image.seek(0)
        pdf=(Path(__file__).resolve().parents[1]/'samples/purchase-order.pdf').read_bytes()
        response=self.client.post('/api/upload',data={'files':[(io.BytesIO(pdf),'Purchase order.PDF','application/pdf'),(image,'scan.png','image/png')]},headers=self.auth,**self.base)
        self.assertEqual(response.status_code,200)
        docs=[self.get_document(id) for id in response.get_json()['ids']]
        self.assertEqual([d['name'] for d in docs],['Purchase order.PDF','scan.png'])
        self.assertTrue(all(d['pages'] and d['status']=='imported' for d in docs))

    def test_edit_revokes_approval_and_stale_revision_rejected(self):
        id,_=self.prepared();doc=self.get_document(id)
        self.assertEqual(self.post('/api/documents/'+id+'/approve',{'revision':doc['reviewRevision']}).status_code,200)
        doc=self.get_document(id);fields=doc['result']['fields'];fields['supplier']='Renamed supplier'
        response=self.post('/api/documents/'+id+'/review',{'fields':fields,'items':doc['result']['items'],'revision':doc['reviewRevision']})
        self.assertEqual(response.status_code,200);self.assertEqual(self.get_document(id)['status'],'validated')
        self.assertEqual(self.post('/api/documents/'+id+'/approve',{'revision':doc['reviewRevision']}).status_code,409)
        self.assertEqual(self.post('/api/export',{'format':'csv','documentIds':[id]}).status_code,400)

    def test_row_add_delete_exact_export_and_exception_label(self):
        id,_=self.prepared();doc=self.get_document(id);result=doc['result'];items=result['items']
        extra={'description':'Second row','quantity':'1','unit_price':'1.00','line_total':'1.00','evidence':None}
        save=self.post('/api/documents/'+id+'/review',{'fields':result['fields'],'items':items+[extra],'revision':doc['reviewRevision']}).get_json()
        self.assertTrue(save['issues'])
        self.assertEqual(self.post('/api/documents/'+id+'/approve',{'revision':save['revision']}).status_code,409)
        approval=self.post('/api/documents/'+id+'/approve',{'revision':save['revision'],'override':True,'note':'Intentional test exception, checked against source'}).get_json()
        self.assertEqual(approval['status'],'approved_with_exceptions')
        exported=self.post('/api/export',{'format':'json','documentIds':[id]}).get_json()
        self.assertEqual(exported[0]['approval'],'approved_with_exceptions');self.assertEqual(len(exported[0]['items']),2)
        doc=self.get_document(id)
        save=self.post('/api/documents/'+id+'/review',{'fields':result['fields'],'items':items,'revision':doc['reviewRevision']}).get_json()
        self.assertFalse(save['issues'])
        self.post('/api/documents/'+id+'/approve',{'revision':save['revision']})
        for format in ['csv','xlsx','json']:
            response=self.post('/api/export',{'format':format,'documentIds':[id]})
            self.assertEqual(response.status_code,200)
        self.assertEqual(len(self.get_document(id)['result']['items']),1)

    def test_empty_extraction_never_accepts_an_override_reason(self):
        id,_=self.prepared();original=self.get_document(id)['result']
        cases=[
            ({name:'   ' for name in original['fields']},original['items']),
            (original['fields'],[]),
        ]
        for fields,items in cases:
            with self.subTest(headers_present=any(v.strip() for v in fields.values()),rows=len(items)):
                doc=self.get_document(id)
                saved=self.post('/api/documents/'+id+'/review',{'fields':fields,'items':items,'revision':doc['reviewRevision']})
                self.assertEqual(saved.status_code,200)
                before=self.get_document(id)
                response=self.post('/api/documents/'+id+'/approve',{'revision':before['reviewRevision'],'override':True,'note':'A written reason cannot bypass missing extracted data.'})
                self.assertEqual(response.status_code,400)
                self.assertIn('Empty extraction cannot be approved',response.get_json()['error'])
                after=self.get_document(id)
                self.assertEqual(after['status'],'needs_review')
                self.assertEqual(after['reviewRevision'],before['reviewRevision'])
                self.assertIsNone(after['approvalNote'])
                self.assertEqual(self.post('/api/export',{'format':'json','documentIds':[id]}).status_code,400)
        audit=self.client.get('/api/documents/'+id+'/audit',headers=self.auth,**self.base).get_json()
        self.assertFalse(any(event['action']=='approved' for event in audit['events']))

    def test_header_only_template_does_not_require_table_rows(self):
        template=copy.deepcopy(server.DEFAULT_TEMPLATE)
        template['name']='Header-only document';template['table']['enabled']=False;template['validation']['enabled']=False
        template_id=self.post('/api/templates',{'template':template}).get_json()['id']
        id=self.post('/api/samples',{'name':'purchase-order.pdf'}).get_json()['id']
        self.post('/api/jobs',{'templateId':template_id,'documentIds':[id]});server.service.start()
        for _ in range(100):
            doc=self.get_document(id)
            if doc['status'] in {'validated','needs_review','failed'}:break
            time.sleep(.02)
        self.assertEqual(doc['status'],'validated')
        self.assertEqual(doc['result']['items'],[])
        self.assertEqual(self.post('/api/documents/'+id+'/approve',{'revision':doc['reviewRevision']}).status_code,200)

    def test_archive_restore_and_no_original_deletion(self):
        id,_=self.prepared();original=server.service.source(server.service.document(id));content=original.read_bytes()
        self.post('/api/documents/'+id+'/archive',{'archived':True})
        self.assertTrue(self.get_document(id)['archived']);self.assertEqual(original.read_bytes(),content)
        self.post('/api/documents/'+id+'/archive',{'archived':False})
        self.assertFalse(self.get_document(id)['archived'])

    def test_duplicate_ignore_is_not_exportable(self):
        id,template=self.prepared();other=self.post('/api/samples',{'name':'purchase-order.pdf'}).get_json()['id']
        self.post('/api/jobs',{'templateId':template,'documentIds':[other]})
        for _ in range(100):
            if self.get_document(other)['status']=='needs_review':break
            time.sleep(.02)
        doc=self.get_document(id)
        self.assertEqual(self.post('/api/documents/'+id+'/approve',{'revision':doc['reviewRevision']}).status_code,409)
        self.assertEqual(self.post('/api/documents/'+id+'/approve',{'revision':doc['reviewRevision'],'override':True,'note':'This must not bypass the explicit duplicate decision.'}).status_code,409)
        self.assertNotIn(self.get_document(id)['status'],{'approved','approved_with_exceptions'})
        self.post('/api/documents/'+other+'/duplicate',{'decision':'ignore','note':'Duplicate source'})
        ignored=self.get_document(other)
        self.assertEqual(self.post('/api/documents/'+other+'/approve',{'revision':ignored['reviewRevision'],'override':True,'note':'This must not turn an ignored duplicate into an export.'}).status_code,400)
        self.assertEqual(self.get_document(other)['status'],'ignored')
        doc=self.get_document(id)
        self.assertEqual(self.post('/api/documents/'+id+'/approve',{'revision':doc['reviewRevision']}).status_code,200)
        self.assertEqual(self.post('/api/export',{'format':'csv','documentIds':[other]}).status_code,400)

    def test_late_duplicate_blocks_export_and_keep_does_not_cover_future_copy(self):
        id,template=self.prepared();doc=self.get_document(id)
        self.assertEqual(self.post('/api/documents/'+id+'/approve',{'revision':doc['reviewRevision']}).status_code,200)
        self.post('/api/samples',{'name':'purchase-order.pdf'})
        response=self.post('/api/export',{'format':'csv','documentIds':[id]})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.get_json()['blockedDocuments'][0]['id'],id)
        self.assertEqual(self.get_document(id)['status'],'needs_review')
        self.post('/api/documents/'+id+'/duplicate',{'decision':'keep','note':'Both current copies intentional'})
        doc=self.get_document(id);self.post('/api/documents/'+id+'/approve',{'revision':doc['reviewRevision']})
        self.assertEqual(self.post('/api/export',{'format':'csv','documentIds':[id]}).status_code,200)
        self.post('/api/samples',{'name':'purchase-order.pdf'})
        self.assertEqual(self.post('/api/export',{'format':'csv','documentIds':[id]}).status_code,409)
        self.assertEqual(self.get_document(id)['status'],'needs_review')

if __name__=='__main__':unittest.main()
