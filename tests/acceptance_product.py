"""Black-box acceptance of an isolated bundled Fieldwork server. Never real user data."""
import csv, io, json, os, secrets, socket, subprocess, sys, time, urllib.request, urllib.error, uuid, zipfile, http.cookiejar
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];QA=ROOT/'qa';FIX=ROOT/'tests'/'fixtures'
EXE=ROOT/'release/Fieldwork.app/Contents/Resources/backend/fieldwork-server'
OCR=ROOT/'release/Fieldwork.app/Contents/Resources/fieldwork-ocr'
REPORT={'started':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'cases':[]}

def check(name,condition,detail=None):
 REPORT['cases'].append({'name':name,'passed':bool(condition),'detail':detail});save()
 if not condition:print('FAIL:',name,flush=True)
def save():(QA/'acceptance_product_report.json').write_text(json.dumps(REPORT,indent=2)+'\n')
class Server:
 def __init__(self,name):
  self.path=ROOT/'work'/name;self.path.mkdir(parents=True,exist_ok=True);self.token=secrets.token_urlsafe(32);self.process=None
 def start(self):
  with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
  self.base='http://127.0.0.1:'+str(self.port)
  env={**os.environ,'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','FIELDWORK_PORT':str(self.port),'FIELDWORK_TOKEN':self.token,'FIELDWORK_DATA_DIR':str(self.path),'FIELDWORK_OCR_BINARY':str(OCR),'PYTHONNOUSERSITE':'1'}
  env.pop('PYTHONHOME',None);env.pop('PYTHONPATH',None)
  self.log=(self.path/'acceptance-server.log').open('ab');self.process=subprocess.Popen([str(EXE)],cwd='/tmp',env=env,stdout=self.log,stderr=self.log)
  for i in range(100):
   try:
    if self.request('/api/health',auth=False)[0]==200:break
   except OSError:pass
   time.sleep(.1)
  else:raise AssertionError('Server did not start')
  self.key=(self.path/'api.key').read_text().strip();return self
 def stop(self):
  if self.process:
   self.process.terminate()
   try:self.process.wait(8)
   except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
   self.log.close();self.process=None
 def request(self,path,body=None,method=None,auth=True,headers=None,raw=False):
  h={**({'Authorization':'Bearer '+self.key} if auth else {}),**(headers or {})}
  if isinstance(body,dict):body=json.dumps(body).encode();h['Content-Type']='application/json'
  req=urllib.request.Request(self.base+path,data=body,method=method or ('POST' if body is not None else 'GET'),headers=h)
  try:r=urllib.request.urlopen(req,timeout=150)
  except urllib.error.HTTPError as e:r=e
  data=r.read();content=data if raw else (json.loads(data) if r.headers.get('Content-Type','').startswith('application/json') else data)
  return r.status,content,dict(r.headers)
 def json(self,path,body=None,method=None):
  status,result,_=self.request(path,body,method);assert 200<=status<300,(path,status,result);return result
 def upload(self,path,route='/api/upload',field='files',form=None):
  boundary='FieldworkQA'+uuid.uuid4().hex;chunks=[]
  for k,v in (form or {}).items():chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
  chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()+path.read_bytes()+b'\r\n');chunks.append(f'--{boundary}--\r\n'.encode())
  return self.request(route,b''.join(chunks),headers={'Content-Type':'multipart/form-data; boundary='+boundary})
 def doc(self,id):return self.json('/api/documents/'+id)
 def process_doc(self,path,tid):
  status,result,_=self.upload(path);assert status==200,(path,status,result);id=result['ids'][0]
  jobs=self.json('/api/jobs',{'documentIds':[id],'templateId':tid})['jobs'];self.wait_jobs(jobs);return self.doc(id)
 def wait_jobs(self,ids):
  for i in range(600):
   jobs=[x for x in self.json('/api/state')['jobs'] if x['id'] in ids]
   if len(jobs)==len(ids) and all(j['status'] not in ('queued','running') for j in jobs):return jobs
   time.sleep(.1)
  raise AssertionError('Job timeout')

def field(name,label,kind='text'):return {'name':name,'label':label,'anchor':label+':','position':'after','required':True,'type':kind}
def template(name,fields,cols=None,header='Description',financial=False,duplicate=None):
 return {'name':name,'fields':[field(*x) for x in fields],'table':{'enabled':bool(cols),'header_anchor':header,'end_anchors':['Subtotal:','Footer:'],'ignore_prefixes':[],'columns':[{'name':n,'label':n,'type':kind,'required':True,'x0':a,'x1':b} for n,kind,a,b in (cols or [])]},'validation':{'enabled':financial,**({k:k for k in ['quantity','unit_price','line_total','subtotal','tax','total']} if financial else {})},'duplicate_fields':duplicate or [],'export_columns':[]}
FINCOLS=[('description','text',.05,.53),('quantity','number',.53,.65),('unit_price','currency',.65,.81),('line_total','currency',.81,.96)]
TEMPLATES={
'invoice-unseen.pdf':template('Invoice acceptance',[('vendor','Vendor'),('invoice_id','Invoice ID'),('date','Invoice date','date'),('email','Customer email','email'),('subtotal','Subtotal','currency'),('tax','Tax','currency'),('total','Total','currency')],FINCOLS,financial=True,duplicate=['vendor','invoice_id']),
'price-list-unseen.pdf':template('Price list acceptance',[('supplier','Supplier'),('effective','Effective date','date')],[('sku','text',.05,.28),('description','text',.28,.72),('price','currency',.72,.96)],header='SKU'),
'custom-form-unseen.pdf':template('Custom form acceptance',[('reference','Reference'),('contact','Contact'),('email','Email','email'),('appointment','Appointment','date')]),
'multipage-unseen.pdf':template('Multipage order acceptance',[('supplier','Supplier'),('order_number','Order number'),('order_date','Order date','date'),('subtotal','Subtotal','currency'),('tax','Tax','currency'),('total','Total','currency')],FINCOLS,financial=True,duplicate=['supplier','order_number'])}

def main():
 name='acceptance-'+time.strftime('%Y%m%d-%H%M%S');s=Server(name).start();other=None
 try:
  check('Unauthenticated state denied',s.request('/api/state',auth=False)[0]==401)
  check('Foreign Origin denied',s.request('/api/state',headers={'Origin':'https://evil.invalid'})[0]==403)
  check('Foreign Host denied',s.request('/api/state',headers={'Host':'evil.invalid'})[0]==403)
  jar=http.cookiejar.CookieJar();browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
  browser.open(s.base+'/?token='+s.token).read()
  check('One-use bootstrap replay denied',s.request('/?token='+s.token,auth=False)[0]==401)
  try:browser.open(urllib.request.Request(s.base+'/api/settings',data=b'{"onboardingDone":true}',headers={'Content-Type':'application/json'}));denied=False
  except urllib.error.HTTPError as error:denied=error.code==403
  check('Cookie write requires CSRF',denied)
  check('Session cookie is HttpOnly and Strict',any('HttpOnly' in c._rest and c._rest.get('SameSite')=='Strict' for c in jar))
  state=s.json('/api/state');check('Fresh workspace empty' ,not state['documents'] and not state['templates']);check('Bundled OCR available',state['ocrAvailable'])
  expected=json.loads((FIX/'expected.json').read_text())['expected'];docs={};tids={}
  for file,t in TEMPLATES.items():
   tid=s.json('/api/templates',{'template':t})['id'];tids[file]=tid;doc=s.process_doc(FIX/file,tid);docs[file]=doc
   (QA/('raw-'+file+'.json')).write_text(json.dumps(doc,indent=2)+'\n')
   actual={'fields':doc['result']['fields'],'items':[{k:v for k,v in r.items() if k!='evidence'} for r in doc['result']['items']]}
   check('Exact extraction '+file,actual==expected[file],{'status':doc['status'],'issues':doc['result']['issues'],'actual':actual})
   check('Clean case passes '+file,doc['status']=='validated',doc['result']['issues'])
  # Portable templates preserve field configuration and create separate identity.
  exported=s.json('/api/templates/'+tids['invoice-unseen.pdf']+'/export')
  imported=s.json('/api/templates/import',{'template':exported.get('template',exported)})
  check('Template import creates new identity',imported['id']!=tids['invoice-unseen.pdf'])
  invoice=docs['invoice-unseen.pdf'];id=invoice['id'];result=invoice['result'];revision=invoice['reviewRevision']
  with_extra=result['items']+[{'description':'QA deliberate added row','quantity':'1','unit_price':'1','line_total':'1'}]
  changed=s.json('/api/documents/'+id+'/review',{'fields':result['fields'],'items':with_extra,'revision':revision})
  fresh=s.doc(id);check('Adding row revalidates',len(fresh['result']['items'])==3 and fresh['status']=='needs_review')
  check('Stale edit rejected',s.request('/api/documents/'+id+'/review',{'fields':result['fields'],'items':result['items'],'revision':revision})[0]==409)
  s.json('/api/documents/'+id+'/review',{'fields':result['fields'],'items':result['items'],'revision':fresh['reviewRevision']});fresh=s.doc(id)
  check('Deleting extra row restores validation',fresh['status']=='validated')
  s.json('/api/documents/'+id+'/approve',{'revision':fresh['reviewRevision']})
  status,csvdata,_=s.request('/api/export',{'format':'csv'},raw=True);rows=list(csv.DictReader(io.StringIO(csvdata.decode('utf-8-sig'))));check('Export only approved exact rows',status==200 and len(rows)==2 and all(r['Source file']=='invoice-unseen.pdf' for r in rows));(QA/'product-approved.csv').write_bytes(csvdata)
  status,book,_=s.request('/api/export',{'format':'xlsx'},raw=True);check('XLSX actual workbook',status==200 and zipfile.is_zipfile(io.BytesIO(book)));(QA/'product-approved.xlsx').write_bytes(book)
  # Duplicate source requires a decision, ignored copy is never approved/exported.
  duplicate=s.process_doc(FIX/'invoice-unseen.pdf',tids['invoice-unseen.pdf']);check('Duplicate held',duplicate['status']=='needs_review' and any(i['code']=='duplicate' for i in duplicate['result']['issues']))
  check('Late duplicate blocks previous approval export',s.request('/api/export',{'format':'csv'},raw=True)[0] in (400,409))
  s.json('/api/documents/'+duplicate['id']+'/duplicate',{'decision':'ignore','note':'Confirmed repeated synthetic source during QA'})
  duplicate=s.doc(duplicate['id']);check('Ignored duplicate cannot approve',s.request('/api/documents/'+duplicate['id']+'/approve',{'revision':duplicate['reviewRevision']})[0]==400)
  fresh=s.doc(id)
  if fresh['status'] not in ('approved','approved_with_exceptions'):s.json('/api/documents/'+id+'/approve',{'revision':fresh['reviewRevision']})
  # Explicit Keep both applies to the reviewed conflict set, not unseen later copies.
  s.json('/api/documents/'+duplicate['id']+'/duplicate',{'decision':'keep','note':'Intentional duplicate copy for conflict-scope QA'})
  s.json('/api/documents/'+id+'/duplicate',{'decision':'keep','note':'Reviewed original and known second copy'})
  fresh=s.doc(id);s.json('/api/documents/'+id+'/approve',{'revision':fresh['reviewRevision']})
  third=s.process_doc(FIX/'invoice-unseen.pdf',tids['invoice-unseen.pdf'])
  check('A new third duplicate invalidates earlier Keep both',s.request('/api/export',{'format':'csv'},raw=True)[0] in (400,409))
  for copy in [duplicate,third]:s.json('/api/documents/'+copy['id']+'/duplicate',{'decision':'ignore','note':'Exclude redundant synthetic copy after conflict test'})
  fresh=s.doc(id);s.json('/api/documents/'+id+'/approve',{'revision':fresh['reviewRevision']})
  # Configurable export mapping and spreadsheet formula safety for nonfinancial form.
  form=docs['custom-form-unseen.pdf'];fv=dict(form['result']['fields']);fv['contact']='=1+1'
  s.json('/api/documents/'+form['id']+'/review',{'fields':fv,'items':[],'revision':form['reviewRevision']});form=s.doc(form['id'])
  s.json('/api/documents/'+form['id']+'/approve',{'revision':form['reviewRevision']})
  st,mapped,_=s.request('/api/export',{'format':'csv','documentIds':[form['id']],'columns':[{'source':'fields.contact','label':'Contact'},{'source':'fields.reference','label':'Request ID'}]},raw=True)
  mr=list(csv.DictReader(io.StringIO(mapped.decode('utf-8-sig'))));check('No-table mapped export and formula escaping',st==200 and len(mr)==1 and mr[0]['Contact']=="'=1+1" and mr[0]['Request ID']=='SR-QA-913')
  # Offline rotated image, all OCR requires source acknowledgement regardless confidence.
  ocr=s.process_doc(FIX/'invoice-scan-rotated.png',tids['invoice-unseen.pdf']);(QA/'raw-ocr-invoice.json').write_text(json.dumps(ocr,indent=2)+'\n')
  check('Rotated OCR values found',ocr['result']['fields']['invoice_id']=='INV-QA-732' and ocr['result']['fields']['total']=='434.41',ocr['result']['fields'])
  check('OCR held for explicit review',ocr['status']=='needs_review' and any(i['code']=='ocr_review' for i in ocr['result']['issues']))
  weak=s.process_doc(FIX/'invoice-scan-weak.png',tids['invoice-unseen.pdf']);check('Weak image not silently approved',weak['status'] in ('needs_review','failed'))
  malformed=FIX/'malformed.pdf';malformed.write_bytes(b'%PDF-not-a-document');check('Malformed PDF rejected',s.upload(malformed)[0]==400)
  # Queue behind several OCR tasks so cancellation is an actual durable queued action.
  ids=[]
  for _ in range(5):ids.append(s.upload(FIX/'invoice-scan-rotated.png')[1]['ids'][0])
  queued=s.json('/api/jobs',{'documentIds':ids,'templateId':tids['invoice-unseen.pdf']})['jobs'];cancelled_id=queued[-1]
  s.json('/api/jobs/'+cancelled_id+'/cancel',{});s.wait_jobs(queued)
  cancelled=next(j for j in s.json('/api/state')['jobs'] if j['id']==cancelled_id);check('Queued job cancellation persists',cancelled['status']=='cancelled')
  retried=s.json('/api/jobs/'+cancelled_id+'/retry',{})['jobs'];jobs=s.wait_jobs(retried);check('Cancelled job can retry safely',all(j['status']=='completed' for j in jobs))
  check('Oversized PDF page rejected before render',s.upload(ROOT/'tests/fixtures/oversized-page.pdf')[0]==400)
  check('Oversized image rejected before decode',s.upload(ROOT/'tests/fixtures/oversized-image.png')[0]==400)
  # Watch a partial file: no import until stable, then one ingestion and original unchanged.
  watchpath=ROOT/'work'/(name+'-watched');watchpath.mkdir();wid=s.json('/api/watches',{'path':str(watchpath),'templateId':tids['custom-form-unseen.pdf'],'enabled':True})['id']
  (watchpath/'broken.pdf').write_bytes(b'%PDF-broken');target=watchpath/'watch-form.pdf';source=(FIX/'custom-form-unseen.pdf').read_bytes();target.write_bytes(source[:100]);time.sleep(1);target.write_bytes(source)
  before=len(s.json('/api/state')['documents'])
  for i in range(150):
   watched=[d for d in s.json('/api/state')['documents'] if d['name']=='watch-form.pdf']
   if watched and watched[0]['status'] not in ('queued','processing'):break
   time.sleep(.1)
  check('Watch imports completed file once',len(watched)==1 and target.read_bytes()==source)
  time.sleep(3.5);check('Watch does not reimport unchanged file',len([d for d in s.json('/api/state')['documents'] if d['name']=='watch-form.pdf'])==1)
  s.json('/api/watches',{'id':wid,'path':str(watchpath),'templateId':tids['custom-form-unseen.pdf'],'enabled':False})
  # Interrupt the application with queued/running work; restart must recover durable job.
  recovery_id=s.upload(FIX/'invoice-scan-rotated.png')[1]['ids'][0]
  recovery_jobs=s.json('/api/jobs',{'documentIds':[recovery_id],'templateId':tids['invoice-unseen.pdf']})['jobs']
  s.stop();s.start();recovered=s.wait_jobs(recovery_jobs)
  check('Interrupted job completes after restart',all(j['status']=='completed' for j in recovered))
  # Persist and restore an actual authenticated export without credentials.
  before=s.json('/api/state');status,backup,_=s.request('/api/backup',raw=True);check('Backup created',status==200);bpath=QA/'product-backup.zip';bpath.write_bytes(backup)
  with zipfile.ZipFile(io.BytesIO(backup)) as z:
   check('Backup excludes auth secrets',all(not n.endswith(('.key','.log')) for n in z.namelist()) and all(s.key.encode() not in z.read(n) for n in z.namelist()))
  key=s.key;s.stop();s.start();after=s.json('/api/state');check('Full restart preserves records and API identity',len(after['documents'])==len(before['documents']) and len(after['templates'])==len(before['templates']) and s.key==key and s.doc(id)['status']=='approved')
  other=Server(name+'-restored').start();otherkey=other.key;status,res,_=other.upload(bpath,'/api/backup/restore','backup',{'confirm':'RESTORE'});check('Fresh restore accepted',status==200,res)
  restored=other.json('/api/state');check('Restore records and approvals preserved',len(restored['documents'])==len(before['documents']) and other.doc(id)['status']=='approved');check('Restore keeps target credentials and pauses watches',other.key==otherkey and other.key!=key and all(not w['enabled'] for w in restored['watches']))
  REPORT['finished']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());save();print(json.dumps({'passed':sum(x['passed'] for x in REPORT['cases']),'failed':sum(not x['passed'] for x in REPORT['cases']),'report':str(QA/'acceptance_product_report.json')}))
 finally:s.stop();other.stop() if other else None
 if any(not x['passed'] for x in REPORT['cases']):raise SystemExit(1)
if __name__=='__main__':main()
