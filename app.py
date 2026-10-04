"""Fieldwork: authenticated loopback API. No external services or network calls."""
import atexit
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from flask import Flask, abort, jsonify, request, send_file, redirect
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from openpyxl import Workbook
from werkzeug.serving import make_server, WSGIRequestHandler
from backend.storage import Store
from backend.service import Service, EXTENSIONS
from backend.engine import DEFAULT_TEMPLATE, validate_result, validate_export_columns, render_original

VERSION='1.0.1'
BASE=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
PORT=int(os.environ.get('FIELDWORK_PORT','4341'))
DATA=Path(os.environ.get('FIELDWORK_DATA_DIR',str(Path.home()/'Library/Application Support/Fieldwork'))).expanduser().resolve()
DATA.mkdir(parents=True,exist_ok=True,mode=0o700)
KEY_PATH=DATA/'api.key'
if not KEY_PATH.exists():
    fd=os.open(KEY_PATH,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as file:file.write(secrets.token_urlsafe(48))
API_KEY=KEY_PATH.read_text().strip()
SIGNER=URLSafeTimedSerializer(API_KEY,salt='fieldwork-local-session-v1')
CSRF=secrets.token_urlsafe(32)
BOOTSTRAP=os.environ.get('FIELDWORK_TOKEN')
BOOTSTRAP_LOCK=threading.Lock()
store=Store(DATA)
ocr_path=os.environ.get('FIELDWORK_OCR_BINARY') or str(BASE/'desktop/fieldwork-ocr')
service=Service(store,ocr_path if Path(ocr_path).is_file() else None)
app=Flask(__name__,static_folder=str(BASE/'static'),static_url_path='/static')
app.config['MAX_CONTENT_LENGTH']=310*1024*1024

def valid_token(value):return isinstance(value,str) and secrets.compare_digest(value,API_KEY)
def signed_session():return SIGNER.dumps({'role':'local-owner','nonce':secrets.token_hex(8)})
def cookie(response):
    response.set_cookie('fieldwork_session',signed_session(),httponly=True,samesite='Strict',max_age=30*86400,path='/')
    return response

def authenticated():
    auth=request.headers.get('Authorization','')
    if auth.startswith('Bearer ') and valid_token(auth[7:]):return 'api'
    try:
        value=SIGNER.loads(request.cookies.get('fieldwork_session',''),max_age=30*86400)
        return 'cookie' if value.get('role')=='local-owner' else None
    except (BadSignature,SignatureExpired):return None

@app.before_request
def security():
    if request.host not in {f'localhost:{PORT}',f'127.0.0.1:{PORT}'}:abort(403,'Use this Mac’s local Fieldwork address')
    origin=request.headers.get('Origin')
    if origin and origin not in {f'http://localhost:{PORT}',f'http://127.0.0.1:{PORT}'}:abort(403,'Origin not allowed')
    if request.path.startswith('/api/'):
        if request.headers.get('Sec-Fetch-Site')=='cross-site':abort(403,'Cross-site access is not allowed')
        if request.path in {'/api/health','/api/session'}:return
        auth=authenticated()
        if not auth:abort(401,'Open Fieldwork from the Mac app, or enter your local API key')
        if request.method not in {'GET','HEAD','OPTIONS'} and auth=='cookie' and not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),CSRF):abort(403,'Refresh Fieldwork before making changes')

@app.after_request
def headers(response):
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Cache-Control':'no-store',
        'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
    return response

@app.errorhandler(Exception)
def error(exc):
    code=getattr(exc,'code',400 if isinstance(exc,(ValueError,KeyError,TypeError)) else 500)
    message=getattr(exc,'description',str(exc)) if code<500 else 'This local operation failed. Your original documents were retained.'
    return jsonify(error=message),code

@app.get('/')
def index():
    global BOOTSTRAP
    provided=request.args.get('token')
    if provided:
        with BOOTSTRAP_LOCK:
            if BOOTSTRAP and secrets.compare_digest(provided,BOOTSTRAP):
                BOOTSTRAP=None
                return cookie(redirect('/'))
        abort(401,'This launch link has expired. Reopen Fieldwork.')
    return app.send_static_file('index.html')

@app.get('/api/health')
def health():return jsonify(application='fieldwork',version=VERSION,ready=True)

login_failures=[]
@app.post('/api/session')
def session():
    now=time.monotonic();login_failures[:]=[t for t in login_failures if t>now-60]
    if len(login_failures)>=10:abort(429,'Too many attempts. Wait a minute and try again.')
    token=(request.get_json(silent=True) or {}).get('token','')
    if not valid_token(token):login_failures.append(now);abort(401,'Invalid local API key')
    return cookie(jsonify(ok=True))

@app.post('/api/logout')
def logout():
    response=jsonify(ok=True);response.delete_cookie('fieldwork_session');return response

def job_dto(row):
    return {'id':row['id'],'documentId':row['document_id'],'templateId':row['template_id'],'status':row['status'],'progress':row['progress'],'error':row['error'],'createdAt':row['created_at'],'cancelRequested':bool(row['cancel_requested'])}

def document_dto(row,conn=None):
    pages=json.loads(row['pages'])
    for page in pages:
        page.pop('words',None);page.pop('image',None)
    job=None
    if conn is not None:
        found=conn.execute('SELECT * FROM jobs WHERE document_id=? ORDER BY created_at DESC,id DESC LIMIT 1',(row['id'],)).fetchone()
        if found:job=job_dto(found)
    return {'id':row['id'],'name':row['name'],'status':row['status'],'archived':bool(row['archived']),'templateId':row['template_id'],'templateVersion':row['template_version'],'templateSnapshot':json.loads(row['template_snapshot']) if row['template_snapshot'] else None,
        'createdAt':row['created_at'],'updatedAt':row['updated_at'],'pages':pages,'result':json.loads(row['result_json']) if row['result_json'] else None,'reviewRevision':row['revision'],'duplicateOf':row['duplicate_of'],'duplicateDecision':row['duplicate_decision'],'approvalNote':row['approval_note'],'job':job}

@app.get('/api/state')
def state():
    with store.connect() as conn:
        documents=[document_dto(row,conn) for row in conn.execute('SELECT * FROM documents ORDER BY created_at DESC,id DESC')]
        templates=[json.loads(row['payload'])|{'archived':bool(row['archived'])} for row in conn.execute('SELECT * FROM templates ORDER BY updated_at DESC')]
        jobs=[job_dto(row) for row in conn.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 200')]
        watches=[{'id':r['id'],'path':r['path'],'templateId':r['template_id'],'enabled':bool(r['enabled']),'error':r['error']} for r in conn.execute('SELECT * FROM watches ORDER BY created_at')]
    return jsonify(csrfToken=CSRF,version=VERSION,documents=documents,templates=templates,defaultTemplate=DEFAULT_TEMPLATE,jobs=jobs,watches=watches,settings=store.setting('settings',{}),samples=[p.name for p in (BASE/'samples').glob('*.pdf')],ocrAvailable=bool(service.ocr_binary))

@app.get('/api/documents/<id>')
def document(id):
    with store.connect() as conn:return jsonify(document_dto(service.document(id,conn),conn))

@app.post('/api/upload')
def upload():
    files=request.files.getlist('files')
    if not 1<=len(files)<=20:raise ValueError('Choose 1–20 files per import')
    template_id=request.form.get('templateId')
    if template_id:service.template(template_id)
    ids=[]
    with service.operation_lock:
        for file in files:ids.append(service.import_file(file.read(),file.filename or 'document.pdf'))
        if template_id:service.queue(ids,template_id)
    return jsonify(ids=ids)

@app.post('/api/samples')
def samples():
    allowed={p.name:p for p in (BASE/'samples').glob('*.pdf')};name=request.get_json()['name']
    if name not in allowed:abort(404,'Sample not found')
    with service.operation_lock:id=service.import_file(allowed[name].read_bytes(),name)
    return jsonify(id=id)

@app.post('/api/templates')
def templates():
    payload=request.get_json()
    with service.operation_lock:return jsonify(service.save_template(payload['template'],payload.get('id')))

@app.post('/api/templates/import')
def import_template():
    template=request.get_json()['template'];template=dict(template);template.pop('id',None);template.pop('version',None)
    with service.operation_lock:return jsonify(service.save_template(template))

@app.get('/api/templates/<id>/export')
def export_template(id):
    with store.connect() as conn:row=conn.execute('SELECT payload FROM templates WHERE id=?',(id,)).fetchone()
    if not row:abort(404,'Template not found')
    return send_file(io.BytesIO(json.dumps(json.loads(row['payload']),indent=2).encode()),mimetype='application/json',as_attachment=True,download_name='fieldwork-template.json')

@app.post('/api/templates/<id>/archive')
def archive_template(id):
    value=request.get_json().get('archived')
    if not isinstance(value,bool):raise ValueError('Archived must be true or false')
    with service.operation_lock,store.connect(True) as conn:
        if value and conn.execute('SELECT 1 FROM watches WHERE template_id=? AND enabled=1',(id,)).fetchone():raise ValueError('Pause watch folders using this template first')
        if conn.execute('UPDATE templates SET archived=?,updated_at=? WHERE id=?',(int(value),time.time(),id)).rowcount!=1:abort(404)
    return jsonify(ok=True)

@app.post('/api/jobs')
def jobs():
    payload=request.get_json()
    with service.operation_lock:ids=service.queue(payload['documentIds'],payload['templateId'])
    return jsonify(jobs=ids)

@app.post('/api/jobs/<id>/cancel')
def cancel_job(id):
    with service.operation_lock,store.connect(True) as conn:
        row=conn.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
        if not row:abort(404)
        if row['status'] in {'queued','running'}:
            conn.execute("UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated_at=? WHERE id=?",(time.time(),id))
            if row['status']=='queued':conn.execute("UPDATE documents SET status='cancelled',updated_at=? WHERE id=?",(time.time(),row['document_id']))
            Store.audit(conn,row['document_id'],'job_cancelled',{'jobId':id})
    return jsonify(ok=True)

@app.post('/api/jobs/<id>/retry')
def retry_job(id):
    with store.connect() as conn:row=conn.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
    if not row or row['status'] not in {'failed','cancelled'}:raise ValueError('Only failed or cancelled jobs can be retried')
    with service.operation_lock:ids=service.queue([row['document_id']],row['template_id'],json.loads(row['template_snapshot']))
    return jsonify(jobs=ids)

@app.post('/api/documents/<id>/archive')
def archive_document(id):
    archived=request.get_json().get('archived')
    if not isinstance(archived,bool):raise ValueError('Archived must be true or false')
    with service.operation_lock,store.connect(True) as conn:
        service.document(id,conn)
        if archived:
            conn.execute("UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE document_id=? AND status IN ('queued','running')",(id,))
        conn.execute('UPDATE documents SET archived=?,updated_at=? WHERE id=?',(int(archived),time.time(),id))
        Store.audit(conn,id,'archived' if archived else 'restored',{})
    return jsonify(ok=True)

def checked_review(conn,id,payload):
    row=service.document(id,conn)
    if row['archived'] or row['status'] in {'processing','queued'}:raise ValueError('Finish or cancel processing and restore this document before reviewing')
    if not row['result_json']:raise ValueError('Process this document first')
    if payload.get('revision')!=row['revision']:abort(409,'This document changed. Refresh it before saving.')
    return row,json.loads(row['result_json']),json.loads(row['template_snapshot'])

@app.post('/api/documents/<id>/review')
def review(id):
    payload=request.get_json()
    with service.operation_lock,store.connect(True) as conn:
        row,result,template=checked_review(conn,id,payload)
        fields,items=payload.get('fields'),payload.get('items')
        field_names={f['name'] for f in template['fields']};column_names={c['name'] for c in template['table'].get('columns',[])}
        if not isinstance(fields,dict) or set(fields)!=field_names or any(not isinstance(v,str) or len(v)>4000 for v in fields.values()):raise ValueError('Invalid header fields')
        if not isinstance(items,list) or len(items)>5000:raise ValueError('Use at most 5,000 rows')
        clean=[]
        for index,item in enumerate(items):
            if not isinstance(item,dict) or any(not isinstance(item.get(n),str) or len(item[n])>8000 for n in column_names):raise ValueError('Invalid table row')
            # Renderer may retain original row evidence or mark new rows with evidence:null.
            evidence=item.get('evidence')
            if evidence not in [r.get('evidence') for r in result['items']]:evidence=None
            clean.append({n:item[n] for n in column_names}|{'evidence':evidence})
        result['fields']=fields;result['items']=clean
        validate_result(result,template)
        duplicate=service.mark_duplicates(conn,id,result,template,row['duplicate_decision'])
        conn.execute('UPDATE documents SET result_json=?,status=?,approval_note=NULL,duplicate_of=?,revision=revision+1,updated_at=? WHERE id=?',(json.dumps(result),result['status'],duplicate,time.time(),id))
        Store.audit(conn,id,'reviewed',{'before':json.loads(row['result_json']),'after':result})
    return jsonify(status=result['status'],issues=result['issues'],revision=row['revision']+1)

@app.post('/api/documents/<id>/duplicate')
def duplicate(id):
    payload=request.get_json();decision=payload.get('decision');note=str(payload.get('note','')).strip()[:2000]
    if decision not in {'keep','ignore'} or len(note)<3:raise ValueError('Choose Keep both or Ignore and give a short reason')
    with service.operation_lock,store.connect(True) as conn:
        row=service.document(id,conn)
        if not row['result_json'] or row['status'] in {'queued','processing'}:raise ValueError('Finish processing first')
        result=json.loads(row['result_json']);template=json.loads(row['template_snapshot']);validate_result(result,template)
        signature=service.duplicate_signature(conn,id,result,template) if decision=='keep' else None
        conn.execute('UPDATE documents SET duplicate_fingerprint=? WHERE id=?',(signature,id))
        found=service.mark_duplicates(conn,id,result,template,decision)
        conn.execute('UPDATE documents SET duplicate_decision=?,duplicate_of=?,status=?,result_json=?,approval_note=NULL,revision=revision+1,updated_at=? WHERE id=?',(decision,found,'ignored' if decision=='ignore' else result['status'],json.dumps(result),time.time(),id))
        Store.audit(conn,id,'duplicate_'+decision,{'note':note,'duplicateOf':found,'conflicts':service.duplicate_conflicts(conn,id,result,template),'fingerprint':signature})
    return jsonify(ok=True)

@app.post('/api/documents/<id>/approve')
def approve(id):
    payload=request.get_json();note=str(payload.get('note','')).strip()[:2000]
    with service.operation_lock,store.connect(True) as conn:
        row,result,template=checked_review(conn,id,payload)
        if row['duplicate_decision']=='ignore':raise ValueError('An ignored duplicate cannot be exported. Choose Keep both first if intentional.')
        validate_result(result,template);duplicate=service.mark_duplicates(conn,id,result,template,row['duplicate_decision'])
        if not any(str(v).strip() for v in result['fields'].values()) or template['table'].get('enabled') and not result['items']:raise ValueError('Empty extraction cannot be approved. Complete required data or retry with a suitable template.')
        if any(i['code']=='duplicate' for i in result['issues']):abort(409,'Resolve the duplicate explicitly using Keep both or Ignore before approval')
        # A deliberate audit override is not relabelled as mathematically validated.
        if result['issues'] and not (payload.get('override') is True and len(note)>=10):return jsonify(error='Review the listed issues. An explicit reason of at least 10 characters is required to override.',issues=result['issues']),409
        status='approved_with_exceptions' if result['issues'] else 'approved'
        conn.execute('UPDATE documents SET status=?,result_json=?,duplicate_of=?,approval_note=?,approval_fingerprint=?,revision=revision+1,updated_at=? WHERE id=?',(status,json.dumps(result),duplicate,note,service.fingerprint(result['issues']),time.time(),id))
        Store.audit(conn,id,'approved',{'override':bool(result['issues']),'issues':result['issues'],'note':note})
    return jsonify(status=status,revision=row['revision']+1)

@app.post('/api/documents/<id>/revoke')
def revoke(id):
    payload=request.get_json()
    with service.operation_lock,store.connect(True) as conn:
        row,result,template=checked_review(conn,id,payload);validate_result(result,template)
        service.mark_duplicates(conn,id,result,template,row['duplicate_decision'])
        conn.execute('UPDATE documents SET status=?,approval_note=NULL,result_json=?,revision=revision+1,updated_at=? WHERE id=?',(result['status'],json.dumps(result),time.time(),id));Store.audit(conn,id,'approval_revoked',{})
    return jsonify(ok=True)

@app.get('/api/documents/<id>/audit')
def audit(id):
    service.document(id)
    with store.connect() as conn:return jsonify(events=[{'id':r['id'],'action':r['action'],'payload':json.loads(r['payload']),'createdAt':r['created_at']} for r in conn.execute('SELECT * FROM audit WHERE document_id=? ORDER BY id DESC',(id,))])

@app.get('/api/documents/<id>/page/<int:page>')
def source_page(id,page):
    row=service.document(id);pages=json.loads(row['pages'])
    if not 1<=page<=len(pages):abort(404)
    info=pages[page-1];image=DATA/info['image'] if info.get('image') else DATA/'rendered'/id/f'original-{page}.png'
    if not image.is_file():
        image.parent.mkdir(parents=True,exist_ok=True)
        temporary=image.with_suffix('.tmp.png');render_original(service.source(row),page,temporary);os.replace(temporary,image)
    return send_file(image,mimetype='image/png')

@app.get('/api/documents/<id>/source')
def source(id):
    row=service.document(id);return send_file(service.source(row),as_attachment=True,download_name=row['name'])

@app.post('/api/export')
def export():
    # Hold the same lock through validation and serialization: imports/extractions cannot
    # introduce a new duplicate between approval validation and the exported snapshot.
    payload=request.get_json()
    with service.operation_lock:
        with store.connect(True) as conn:
            documents=export_selection(conn,payload)
            blocked=[]
            for row in documents:
                result=json.loads(row['result_json']);template=json.loads(row['template_snapshot'])
                validate_result(result,template)
                duplicate=service.mark_duplicates(conn,row['id'],result,template,row['duplicate_decision'])
                if any(i['code']=='duplicate' for i in result['issues']) or result['issues'] and service.fingerprint(result['issues'])!=row['approval_fingerprint']:
                    blocked.append({'id':row['id'],'name':row['name'],'issues':result['issues']})
                    conn.execute("UPDATE documents SET status='needs_review',result_json=?,duplicate_of=?,approval_note=NULL,approval_fingerprint=NULL,revision=revision+1,updated_at=? WHERE id=?",(json.dumps(result),duplicate,time.time(),row['id']))
                    Store.audit(conn,row['id'],'approval_invalidated_at_export',{'issues':result['issues']})
        # Return after commit, so the revoked approvals remain visible in the library.
        if blocked:return jsonify(error='Export cancelled. Review new issues in: '+', '.join(r['name'] for r in blocked),blockedDocuments=blocked),409
        return export_data(payload)

def export_selection(conn,payload):
    if payload.get('format') not in {'csv','xlsx','json'}:raise ValueError('Choose CSV, Excel or JSON')
    ids=payload.get('documentIds')
    documents=conn.execute("SELECT * FROM documents WHERE status IN ('approved','approved_with_exceptions') AND archived=0 AND COALESCE(duplicate_decision,'')<>'ignore' ORDER BY created_at,id").fetchall()
    if ids is not None:
        if not isinstance(ids,list) or len(ids)>5000:raise ValueError('Invalid document selection')
        documents=[r for r in documents if r['id'] in ids]
        if set(ids)!={r['id'] for r in documents}:raise ValueError('The selection contains unapproved, ignored or archived documents. Export cancelled.')
    if not documents:raise ValueError('Approve at least one document before exporting')
    return documents

def export_data(payload):
    format=payload.get('format')
    with service.operation_lock,store.connect() as conn:
        documents=export_selection(conn,payload)
        template=json.loads(documents[0]['template_snapshot']);columns=payload.get('columns') or template.get('export_columns')
        if not columns:
            fields=list(dict.fromkeys(k for r in documents for k in json.loads(r['result_json'])['fields']))
            items=list(dict.fromkeys(k for r in documents for item in json.loads(r['result_json'])['items'] for k in item if k!='evidence'))
            columns=[{'source':'source_file','label':'Source file'}]+[{'source':'fields.'+k,'label':k} for k in fields]+[{'source':'items.'+k,'label':k} for k in items]+[{'source':'review_note','label':'Review note'}]
        validate_export_columns(columns)
        rows=[];structured=[]
        for document in documents:
            result=json.loads(document['result_json']);structured.append({'source_file':document['name'],'fields':result['fields'],'items':[{k:v for k,v in r.items() if k!='evidence'} for r in result['items']],'approval':document['status'],'review_note':document['approval_note'],'issues':result['issues']})
            for item in result['items'] or [{}]:
                cells=[]
                for col in columns:
                    source=col['source']
                    value=document['name'] if source=='source_file' else document['approval_note'] or '' if source=='review_note' else (result['fields'] if source.startswith('fields.') else item).get(source.split('.',1)[1],'')
                    value=str(value)
                    # Numeric negatives are safe; other spreadsheet formulas must stay literal.
                    if value.lstrip().startswith(('=','+','@','\t','\r')) or value.startswith('-') and not value[1:].replace('.','',1).isdigit():value="'"+value
                    cells.append(value)
                rows.append(cells)
        Store.audit(conn,None,'exported',{'format':format,'documentIds':[r['id'] for r in documents],'rows':len(rows)})
    output=io.BytesIO()
    if format=='json':output.write(json.dumps(structured,indent=2,ensure_ascii=False).encode());mime='application/json'
    elif format=='csv':
        text=io.StringIO();writer=csv.writer(text);writer.writerows([[c['label'] for c in columns]]+rows);output.write(text.getvalue().encode('utf-8-sig'));mime='text/csv'
    else:
        book=Workbook();sheet=book.active;sheet.title='Approved data'
        for row in [[c['label'] for c in columns]]+rows:sheet.append(row)
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        book.save(output);mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    output.seek(0);return send_file(output,mimetype=mime,as_attachment=True,download_name='fieldwork-approved.'+format)

@app.post('/api/watches')
def watches():
    payload=request.get_json();directory=Path(str(payload.get('path',''))).expanduser()
    if not directory.is_absolute() or not directory.is_dir():raise ValueError('Choose an existing absolute folder path')
    directory=directory.resolve()
    if directory==DATA or DATA in directory.parents or directory in DATA.parents:raise ValueError('Choose a document folder outside Fieldwork application data, not a parent of it')
    service.template(payload['templateId']);id=payload.get('id') or str(uuid.uuid4());enabled=payload.get('enabled',True)
    if not isinstance(enabled,bool):raise ValueError('Enabled must be true or false')
    with service.operation_lock,store.connect(True) as conn:
        old=conn.execute('SELECT * FROM watches WHERE id=?',(id,)).fetchone()
        if payload.get('id') and not old:raise ValueError('Watch folder not found')
        try:conn.execute('INSERT INTO watches(id,path,template_id,enabled,created_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path,template_id=excluded.template_id,enabled=excluded.enabled,error=NULL',(id,str(directory),payload['templateId'],int(enabled),time.time()))
        except Exception:raise ValueError('This folder already has a watcher')
        Store.audit(conn,None,'watch_saved',{'id':id,'path':str(directory),'enabled':enabled})
    return jsonify(id=id)

@app.delete('/api/watches/<id>')
def delete_watch(id):
    with service.operation_lock,store.connect(True) as conn:
        conn.execute('DELETE FROM watches WHERE id=?',(id,));Store.audit(conn,None,'watch_removed',{'id':id})
    return jsonify(ok=True)

@app.post('/api/settings')
def settings():
    payload=request.get_json()
    if not isinstance(payload,dict) or not set(payload)<={'onboardingDone','defaultTemplateId','exportColumns'}:raise ValueError('Unknown setting')
    if 'onboardingDone' in payload and not isinstance(payload['onboardingDone'],bool):raise ValueError('Invalid onboarding setting')
    if payload.get('defaultTemplateId'):service.template(payload['defaultTemplateId'])
    if 'exportColumns' in payload:validate_export_columns(payload['exportColumns'])
    with service.operation_lock:store.set_setting('settings',store.setting('settings',{})|payload)
    return jsonify(ok=True)

@app.get('/api/backup')
def backup():return send_file(service.backup(),mimetype='application/zip',as_attachment=True,download_name='fieldwork-backup-'+time.strftime('%Y-%m-%d')+'.zip')

@app.post('/api/backup/restore')
def restore():
    if request.form.get('confirm')!='RESTORE' or 'backup' not in request.files:raise ValueError('Confirm RESTORE and choose a Fieldwork backup')
    with service.operation_lock:previous=service.restore(request.files['backup'].read())
    return jsonify(ok=True,safetyBackup=previous)

@app.post('/api/security/key')
def key():return jsonify(token=API_KEY)

class QuietHandler(WSGIRequestHandler):
    def log(self,type,message,*args):pass

def main():
    service.start();atexit.register(service.close)
    server=make_server('127.0.0.1',PORT,app,threaded=True,request_handler=QuietHandler)
    def shutdown(signum,frame):
        service.close()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    print(f'Fieldwork {VERSION} ready on 127.0.0.1:{PORT}',flush=True)
    try:server.serve_forever()
    finally:
        service.close();server.server_close()
        for name in ['worker','watcher']:
            thread=getattr(service,name,None)
            if thread:thread.join(3)

if __name__=='__main__':main()
