import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from contextlib import closing
from .engine import metadata, read_pages, extract_pages, validate_result, validate_template, issue
from .storage import Store

EXTENSIONS={'.pdf','.png','.jpg','.jpeg','.tif','.tiff'}
MAX_FILE=25*1024*1024

class Service:
    def __init__(self,store,ocr_binary=None):
        self.store=store;self.ocr_binary=ocr_binary;self.stop=threading.Event();self.wake=threading.Event();self.operation_lock=threading.RLock();self.stability={}

    def start(self):
        self.worker=threading.Thread(target=self.work_loop,daemon=True,name='fieldwork-extraction');self.worker.start()
        self.watcher=threading.Thread(target=self.watch_loop,daemon=True,name='fieldwork-watch');self.watcher.start()

    def close(self): self.stop.set();self.wake.set()

    def document(self,id,conn=None):
        if conn is not None:
            row=conn.execute('SELECT * FROM documents WHERE id=?',(id,)).fetchone()
        else:
            with self.store.connect() as c:row=c.execute('SELECT * FROM documents WHERE id=?',(id,)).fetchone()
        if not row:raise ValueError('Document not found')
        return row

    def source(self,row):return self.store.root/'uploads'/(row['id']+row['extension'])

    def template(self,id):
        with self.store.connect() as conn:row=conn.execute('SELECT * FROM templates WHERE id=? AND archived=0',(id,)).fetchone()
        if not row:raise ValueError('Choose an active template')
        return json.loads(row['payload'])

    def save_template(self,template,id=None):
        template=validate_template(json.loads(json.dumps(template)));now=time.time()
        with self.store.connect(True) as conn:
            old=conn.execute('SELECT * FROM templates WHERE id=?',(id,)).fetchone() if id else None
            if id and not old:raise ValueError('Template not found')
            id=id or str(uuid.uuid4());version=(old['version']+1) if old else 1
            template.update(id=id,version=version)
            conn.execute('INSERT INTO templates(id,name,version,payload,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,version=excluded.version,payload=excluded.payload,updated_at=excluded.updated_at', (id,template['name'],version,json.dumps(template),now,now))
            conn.execute('INSERT INTO template_versions VALUES(?,?,?,?)',(id,version,json.dumps(template),now))
            Store.audit(conn,None,'template_saved',{'id':id,'version':version})
        return {'id':id,'version':version}

    def import_file(self,data,name):
        name=Path(name).name[:200];extension=Path(name).suffix.lower()
        if extension not in EXTENSIONS or not 1<=len(data)<=MAX_FILE:raise ValueError('Use PDF, PNG, JPEG or TIFF files up to 25 MB each')
        if extension=='.pdf' and not data.startswith(b'%PDF-'):raise ValueError('This file is not a PDF')
        id=str(uuid.uuid4());target=self.store.root/'uploads'/(id+extension);target.write_bytes(data);os.chmod(target,0o600)
        try:pages=metadata(target)
        except Exception:
            target.unlink(missing_ok=True);raise ValueError('Cannot read this file. Use an unlocked PDF/image with at most 100 pages.')
        now=time.time()
        with self.store.connect(True) as conn:
            conn.execute('INSERT INTO documents(id,name,extension,hash,pages,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(id,name,extension,hashlib.sha256(data).hexdigest(),json.dumps(pages),now,now))
            Store.audit(conn,id,'imported',{'name':name,'bytes':len(data)})
        return id

    def queue(self,ids,template_id,snapshot=None):
        if not isinstance(ids,list) or not 1<=len(ids)<=100:raise ValueError('Choose 1–100 documents')
        template=snapshot or self.template(template_id);jobs=[];now=time.time()
        with self.store.connect(True) as conn:
            for id in dict.fromkeys(ids):
                row=self.document(id,conn)
                if row['archived']:raise ValueError('Restore archived documents before processing')
                current=conn.execute("SELECT id FROM jobs WHERE document_id=? AND status IN ('queued','running')",(id,)).fetchone()
                if current:jobs.append(current['id']);continue
                job_id=str(uuid.uuid4())
                conn.execute('INSERT INTO jobs(id,document_id,template_id,template_snapshot,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(job_id,id,template_id,json.dumps(template),'queued',now,now))
                conn.execute("UPDATE documents SET status='queued',approval_note=NULL,duplicate_decision=NULL,revision=revision+1,updated_at=? WHERE id=?",(now,id))
                Store.audit(conn,id,'queued',{'jobId':job_id,'templateId':template_id,'version':template.get('version')});jobs.append(job_id)
        self.wake.set();return jobs

    def cancelled(self,id):
        if self.stop.is_set():return True
        with self.store.connect() as conn:
            row=conn.execute('SELECT cancel_requested,status FROM jobs WHERE id=?',(id,)).fetchone()
            return not row or bool(row['cancel_requested']) or row['status']=='cancelled'

    def progress(self,id,value):
        with self.store.connect() as conn:conn.execute('UPDATE jobs SET progress=?,updated_at=? WHERE id=?',(value,time.time(),id))

    def mark_duplicates(self,conn,id,result,template,decision=None):
        result['issues']=[i for i in result['issues'] if i['code']!='duplicate']
        current=self.document(id,conn);duplicate=None
        keys=template.get('duplicate_fields',[])
        for other in conn.execute('SELECT id,hash,result_json,duplicate_decision FROM documents WHERE id<>? AND archived=0 ORDER BY created_at,id',(id,)):
            if other['duplicate_decision']=='ignore':continue
            exact=other['hash']==current['hash']
            if not exact and keys and other['result_json']:
                other_fields=json.loads(other['result_json']).get('fields',{})
                exact=all(str(result['fields'].get(n,'')).strip() and str(result['fields'].get(n,'')).strip().casefold()==str(other_fields.get(n,'')).strip().casefold() for n in keys)
            if exact:duplicate=other['id'];break
        if duplicate and decision!='keep':result['issues'].append(issue('duplicate','', 'Another document has the same source file or configured identifiers. Choose Keep both or Ignore this duplicate before approval.','review'))
        result['status']='needs_review' if result['issues'] else 'validated'
        return duplicate

    def work_loop(self):
        while not self.stop.is_set():
            with self.operation_lock,self.store.connect(True) as conn:
                job=conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
                if job:
                    conn.execute("UPDATE jobs SET status='running',attempts=attempts+1,progress=1,updated_at=? WHERE id=?",(time.time(),job['id']))
                    conn.execute("UPDATE documents SET status='processing',updated_at=? WHERE id=?",(time.time(),job['document_id']))
            if not job:
                self.wake.wait(1);self.wake.clear();continue
            try:
                row=self.document(job['document_id']);template=json.loads(job['template_snapshot'])
                directory=self.store.root/'rendered'/row['id']/job['id']
                pages=read_pages(self.source(row),directory,self.ocr_binary,lambda value:self.progress(job['id'],value),lambda:self.cancelled(job['id']))
                if self.cancelled(job['id']):raise InterruptedError('Cancelled')
                result=extract_pages(pages,template);self.progress(job['id'],90)
                # Paths are generated internally; preserve each extraction's immutable source view.
                for page in pages:
                    if page.get('image'):page['image']=str((directory/page['image']).relative_to(self.store.root))
                with self.operation_lock,self.store.connect(True) as conn:
                    latest=conn.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()
                    if latest['cancel_requested']:raise InterruptedError('Cancelled')
                    duplicate=self.mark_duplicates(conn,row['id'],result,template)
                    now=time.time()
                    conn.execute('UPDATE documents SET pages=?,template_id=?,template_version=?,template_snapshot=?,raw_json=?,result_json=?,status=?,duplicate_of=?,duplicate_decision=NULL,approval_note=NULL,revision=revision+1,updated_at=? WHERE id=?',(json.dumps(pages),job['template_id'],template.get('version',1),json.dumps(template),json.dumps(result),json.dumps(result),result['status'],duplicate,now,row['id']))
                    conn.execute("UPDATE jobs SET status='completed',progress=100,error=NULL,updated_at=? WHERE id=?",(now,job['id']))
                    Store.audit(conn,row['id'],'extracted',{'jobId':job['id'],'templateVersion':template.get('version'),'issues':result['issues']})
            except InterruptedError:
                with self.store.connect(True) as conn:
                    # App shutdown recovers queued work; deliberate cancellation stays cancelled.
                    deliberate=conn.execute('SELECT cancel_requested FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]
                    status='cancelled' if deliberate else 'queued'
                    conn.execute('UPDATE jobs SET status=?,progress=0,updated_at=? WHERE id=?',(status,time.time(),job['id']))
                    conn.execute('UPDATE documents SET status=?,updated_at=? WHERE id=?',(status,time.time(),job['document_id']))
            except Exception as exc:
                with self.store.connect(True) as conn:
                    conn.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=?",(str(exc)[:500],time.time(),job['id']))
                    conn.execute("UPDATE documents SET status='failed',updated_at=? WHERE id=?",(time.time(),job['document_id']))

    def watch_loop(self):
        while not self.stop.wait(3):
            with self.store.connect() as conn:watches=conn.execute('SELECT * FROM watches WHERE enabled=1').fetchall()
            for watch in watches:
                try:
                    directory=Path(watch['path'])
                    if not directory.is_dir():raise ValueError('Folder is unavailable. Reconnect the disk or choose another folder.')
                    errors=[]
                    for file in directory.iterdir():
                        if self.stop.is_set():break
                        if file.is_symlink() or not file.is_file() or file.suffix.lower() not in EXTENSIONS:continue
                        info=file.stat();key=(watch['id'],str(file));size=(info.st_size,info.st_mtime_ns)
                        if not 1<=info.st_size<=MAX_FILE:
                            errors.append(f'{file.name}: empty or larger than 25 MB');continue
                        previous=self.stability.get(key);self.stability[key]=size
                        if previous!=size:continue
                        data=file.read_bytes();after=file.stat()
                        if (after.st_size,after.st_mtime_ns)!=size:continue
                        fingerprint=hashlib.sha256(data).hexdigest()
                        with self.operation_lock,self.store.connect() as conn:
                            current=conn.execute('SELECT enabled FROM watches WHERE id=?',(watch['id'],)).fetchone()
                            seen=conn.execute('SELECT 1 FROM watch_seen WHERE watch_id=? AND path=? AND fingerprint=?',(watch['id'],str(file),fingerprint)).fetchone()
                        if not current or not current['enabled'] or seen:continue
                        try:
                            with self.operation_lock:
                                id=self.import_file(data,file.name)
                                self.queue([id],watch['template_id'])
                                with self.store.connect(True) as conn:conn.execute('INSERT OR IGNORE INTO watch_seen VALUES(?,?,?,?)',(watch['id'],str(file),fingerprint,id))
                        except Exception as exc:
                            # A bad/partial file must not starve unrelated files in the same folder.
                            # It stays eligible for retry; no source file is moved, deleted or marked successful.
                            errors.append(f'{file.name}: {str(exc)[:150]}')
                    with self.store.connect() as conn:conn.execute('UPDATE watches SET error=? WHERE id=?',('; '.join(errors[:3])[:500] or None,watch['id']))
                except Exception as exc:
                    with self.store.connect() as conn:conn.execute('UPDATE watches SET error=? WHERE id=?',(str(exc)[:300],watch['id']))

    def backup(self):
        output=io.BytesIO()
        with self.operation_lock:
            temporary=self.store.root/'backups'/('snapshot-'+str(uuid.uuid4())+'.sqlite')
            with self.store.connect() as source:
                target=sqlite3.connect(temporary)
                try:source.backup(target)
                finally:target.close()
            try:
                with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('manifest.json',json.dumps({'application':'fieldwork','format':1,'createdAt':time.time()}))
                    archive.write(temporary,'fieldwork.sqlite')
                    with closing(sqlite3.connect(temporary)) as snapshot:
                        for id,extension,pages in snapshot.execute('SELECT id,extension,pages FROM documents'):
                            source=self.store.root/'uploads'/(id+extension)
                            if not source.is_file():raise ValueError('A source file is missing. Backup aborted rather than silently omitting it.')
                            archive.write(source,'uploads/'+source.name)
                            for page in json.loads(pages):
                                image=page.get('image')
                                if image and (self.store.root/image).is_file():archive.write(self.store.root/image,image)
            finally:temporary.unlink(missing_ok=True)
        output.seek(0);return output

    def restore(self,data):
        # Only a checked application backup can replace data. Always retain a pre-restore copy.
        with self.operation_lock,self.store.connect() as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():raise ValueError('Cancel or finish active jobs before restoring a backup')
            if conn.execute('SELECT 1 FROM watches WHERE enabled=1 LIMIT 1').fetchone():raise ValueError('Pause watch folders before restoring a backup')
        if len(data)>300*1024*1024:raise ValueError('Backup exceeds the 300 MB prototype restore limit')
        temp=self.store.root/'backups'/('restore-'+str(uuid.uuid4()));temp.mkdir()
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos=archive.infolist()
                if len(infos)>20000 or sum(i.file_size for i in infos)>1024*1024*1024:raise ValueError('Backup is too large')
                names={i.filename for i in infos}
                if not {'manifest.json','fieldwork.sqlite'}<=names:raise ValueError('Not a Fieldwork backup')
                manifest=json.loads(archive.read('manifest.json'))
                if manifest.get('application')!='fieldwork' or manifest.get('format')!=1:raise ValueError('Unsupported backup format')
                for info in infos:
                    name=Path(info.filename)
                    if name.is_absolute() or '..' in name.parts or info.filename not in {'manifest.json','fieldwork.sqlite'} and not info.filename.startswith(('uploads/','rendered/')) or (info.external_attr>>16)&0o170000==0o120000:raise ValueError('Unsafe backup path')
                archive.extractall(temp)
            with closing(sqlite3.connect(temp/'fieldwork.sqlite')) as candidate:
                if candidate.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():raise ValueError('Backup contains unsupported executable database objects')
                if candidate.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup database is damaged')
                if candidate.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]!='1':raise ValueError('Unsupported database version')
                for id,ext,pages,fingerprint in candidate.execute('SELECT id,extension,pages,hash FROM documents'):
                    if not re_uuid(id) or ext not in EXTENSIONS or not (temp/'uploads'/(id+ext)).is_file():raise ValueError('Backup has missing or invalid source files')
                    if hashlib.sha256((temp/'uploads'/(id+ext)).read_bytes()).hexdigest()!=fingerprint:raise ValueError('Backup source checksum does not match its database')
                    for page in json.loads(pages):
                        image=page.get('image')
                        if image and (not isinstance(image,str) or Path(image).is_absolute() or '..' in Path(image).parts or not image.startswith('rendered/')):raise ValueError('Unsafe image reference')
                for (payload,) in candidate.execute('SELECT payload FROM template_versions'):validate_template(json.loads(payload))
            with self.operation_lock:
                previous=self.backup();backup_path=self.store.root/'backups'/('before-restore-'+str(int(time.time()))+'.zip');backup_path.write_bytes(previous.read());os.chmod(backup_path,0o600)
                # SQLite backup replaces tables atomically; source filenames are UUIDs and never overwritten by other content.
                for directory in ['uploads','rendered']:
                    if (temp/directory).exists():shutil.copytree(temp/directory,self.store.root/directory,dirs_exist_ok=True)
                source=sqlite3.connect(temp/'fieldwork.sqlite');target=sqlite3.connect(self.store.path)
                try:source.backup(target)
                finally:source.close();target.close()
                with self.store.connect(True) as conn:
                    conn.execute('UPDATE watches SET enabled=0,error=?',('Restored watch folders are paused. Review paths before enabling.',))
                    conn.execute("UPDATE jobs SET status='queued',progress=0 WHERE status='running'")
                    Store.audit(conn,None,'backup_restored',{'safetyBackup':backup_path.name})
                self.wake.set()
            return backup_path.name
        finally:shutil.rmtree(temp,ignore_errors=True)

def re_uuid(value):
    try:return str(uuid.UUID(value))==value
    except (ValueError,TypeError):return False
