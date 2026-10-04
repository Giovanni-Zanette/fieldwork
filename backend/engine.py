"""Deterministic, local-only extraction and exact decimal validation."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import math
from pathlib import Path
import re
import subprocess
import time
import pdfplumber
from PIL import Image, ImageSequence, ImageOps

TYPES = {'text', 'number', 'currency', 'date', 'email'}
IDENTIFIER = re.compile(r'^[a-z][a-z0-9_]{0,49}$')
DEFAULT_TEMPLATE = {'name': 'Purchase orders', 'fields': [
    {'name': n, 'label': label, 'type': kind, 'required': True, 'anchor': anchor, 'position': 'after'}
    for n, label, kind, anchor in [('supplier','Supplier','text','Supplier:'),('order_number','Order number','text','Order number:'),('order_date','Order date','date','Order date:'),('subtotal','Subtotal','currency','Subtotal:'),('tax','Tax','currency','Tax:'),('total','Total','currency','Total:')]],
    'table': {'enabled': True, 'header_anchor': 'Description', 'end_anchors': ['Subtotal:', 'Continued on', 'Page '], 'ignore_prefixes': [], 'columns': [
        {'name':n,'label':label,'type':kind,'required':True,'x0':x0,'x1':x1} for n,label,kind,x0,x1 in [('description','Description','text',.06,.55),('quantity','Quantity','number',.55,.68),('unit_price','Unit price','currency',.68,.82),('line_total','Line total','currency',.82,.95)]]},
    'validation': {'enabled': True, 'quantity':'quantity','unit_price':'unit_price','line_total':'line_total','subtotal':'subtotal','tax':'tax','total':'total'},
    'duplicate_fields': ['supplier','order_number'], 'export_columns': []}

def validate_template(template):
    if not isinstance(template, dict):
        raise ValueError('Template must be an object')
    name = template.get('name')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('Template name must be 1–100 characters')
    fields = template.get('fields')
    if not isinstance(fields, list) or not 1 <= len(fields) <= 40:
        raise ValueError('Use 1–40 header fields')
    def definitions(defs, table=False):
        names = set()
        for f in defs:
            if not isinstance(f, dict) or not IDENTIFIER.fullmatch(str(f.get('name',''))) or f['name'] in names or f.get('type') not in TYPES:
                raise ValueError('Fields need unique lowercase identifiers and a supported type')
            names.add(f['name'])
            if not isinstance(f.get('label', f['name']), str) or len(f.get('label', '')) > 100 or not isinstance(f.get('required', False), bool):
                raise ValueError('Invalid field label or required marker')
            if not table and (not isinstance(f.get('anchor'), str) or not 1 <= len(f['anchor'].strip()) <= 150 or f.get('position','after') not in {'after','below'}):
                raise ValueError('Each header needs a literal anchor and after/below position')
        return names
    field_names = definitions(fields)
    table = template.get('table', {'enabled':False,'columns':[]})
    template['table'] = table
    column_names = set()
    if table.get('enabled'):
        columns = table.get('columns')
        if not isinstance(columns, list) or not 1 <= len(columns) <= 20:
            raise ValueError('Use 1–20 table columns')
        column_names = definitions(columns, True)
        previous = 0
        for col in columns:
            a,b=col.get('x0'),col.get('x1')
            if isinstance(a,bool) or isinstance(b,bool) or not isinstance(a,(float,int)) or not isinstance(b,(float,int)) or not 0 <= a < b <= 1 or a < previous-.0001:
                raise ValueError('Table columns must be ordered, nonoverlapping fractions from 0 to 1')
            previous=b
        if not isinstance(table.get('header_anchor'),str) or not 1 <= len(table['header_anchor'].strip()) <= 150:
            raise ValueError('The repeating table needs a header anchor')
        for key in ['end_anchors','ignore_prefixes']:
            values=table.setdefault(key,[])
            if not isinstance(values,list) or len(values)>20 or any(not isinstance(v,str) or not 1<=len(v.strip())<=150 for v in values):
                raise ValueError('Use up to 20 nonempty literal table boundaries')
    validation=template.setdefault('validation',{'enabled':False})
    if validation.get('enabled'):
        for key in ['quantity','unit_price','line_total']:
            if validation.get(key) and validation[key] not in column_names:
                raise ValueError('Arithmetic column mappings must refer to a table column')
        for key in ['subtotal','tax','total']:
            if validation.get(key) and validation[key] not in field_names:
                raise ValueError('Arithmetic total mappings must refer to a header field')
    keys=template.setdefault('duplicate_fields',[])
    if not isinstance(keys,list) or any(n not in field_names for n in keys):
        raise ValueError('Duplicate identifiers must refer to header fields')
    validate_export_columns(template.get('export_columns',[]))
    return template

def validate_export_columns(columns):
    if not isinstance(columns,list) or len(columns)>100:
        raise ValueError('Use at most 100 export columns')
    for c in columns:
        if not isinstance(c,dict) or not isinstance(c.get('label'),str) or not 1<=len(c['label'])<=100 or not re.fullmatch(r'(source_file|review_note|fields\.[a-z][a-z0-9_]{0,49}|items\.[a-z][a-z0-9_]{0,49})',str(c.get('source',''))):
            raise ValueError('Invalid export column mapping')
    return columns

def decimal_value(value):
    text=str(value).strip().replace('\u00a0','').replace(' ','')
    text=re.sub(r'^(?:ZAR|R|USD|\$|EUR|€|GBP|£)', '', text, flags=re.I)
    if not re.fullmatch(r'-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?',text):
        raise ValueError('Use a decimal number with a dot, for example 1250.00')
    result=Decimal(text.replace(',',''))
    if not result.is_finite() or abs(result)>Decimal('1000000000000'):
        raise ValueError('Number outside supported range')
    return result

def lines_for(words):
    groups=[]
    for word in sorted(words,key=lambda w:(w['top'],w['x0'])):
        group=next((g for g in reversed(groups[-4:]) if abs(g[0]['top']-word['top'])<=max(3,(word['bottom']-word['top'])*.3)),None)
        if group is None: groups.append([word])
        else: group.append(word)
    return [sorted(g,key=lambda w:w['x0']) for g in sorted(groups,key=lambda g:g[0]['top'])]

def text(words): return ' '.join(w['text'] for w in words)
def bbox(words): return [min(w['x0'] for w in words),min(w['top'] for w in words),max(w['x1'] for w in words),max(w['bottom'] for w in words)]
def issue(code,path,message,severity='error'): return {'code':code,'path':path,'message':message,'severity':severity}

def anchor_matches(value,anchor):
    value=value.casefold();anchor=anchor.strip().casefold()
    if not value.startswith(anchor):return False
    # A column headed SKU must not swallow data such as SKU-123 as another header.
    return len(value)==len(anchor) or value[len(anchor)].isspace() or anchor.endswith((':','='))

def check_render_size(width,height,pdf=False):
    scale=200/72 if pdf else 1
    w=float(width)*scale;h=float(height)*scale
    if not math.isfinite(w) or not math.isfinite(h) or min(w,h)<=0 or max(w,h)>16000 or w*h>40000000:
        raise ValueError('Page exceeds the safe rendering limit of 40 megapixels or 16,000 pixels per edge')

def metadata(path):
    path=Path(path)
    if path.suffix.lower()=='.pdf':
        with pdfplumber.open(path) as pdf:
            if not 1<=len(pdf.pages)<=100: raise ValueError('Use PDFs with 1–100 pages')
            for page in pdf.pages:check_render_size(page.width,page.height,True)
            return [{'page':i+1,'width':p.width,'height':p.height,'lines':[{'text':text(line),'bbox':bbox(line)} for line in lines_for(p.extract_words(x_tolerance=2,y_tolerance=3))]} for i,p in enumerate(pdf.pages)]
    with Image.open(path) as image:
        if getattr(image,'n_frames',1)>100: raise ValueError('Use at most 100 image pages')
        pages=[]
        for i,frame in enumerate(ImageSequence.Iterator(image)):
            check_render_size(frame.width,frame.height)
            pages.append({'page':i+1,'width':frame.width,'height':frame.height,'lines':[]})
        return pages

def render_original(path,page,outfile):
    path=Path(path)
    if path.suffix.lower()=='.pdf':
        with pdfplumber.open(path) as pdf:
            check_render_size(pdf.pages[page-1].width,pdf.pages[page-1].height,True)
            pdf.pages[page-1].to_image(resolution=200).original.save(outfile,format='PNG')
    else:
        with Image.open(path) as im:
            im.seek(page-1)
            check_render_size(im.width,im.height)
            ImageOps.exif_transpose(im).convert('RGB').save(outfile,format='PNG')

def run_ocr(command,cancelled):
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    start=time.monotonic()
    try:
        while True:
            if cancelled():raise InterruptedError('Cancelled')
            if time.monotonic()-start>120:raise subprocess.TimeoutExpired(command,120)
            try:
                stdout,stderr=process.communicate(timeout=.25)
                if process.returncode:raise subprocess.CalledProcessError(process.returncode,command,stdout,stderr)
                return stdout
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.communicate(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.communicate()

def read_pages(path,render_dir,ocr_binary=None,progress=lambda value:None,cancelled=lambda:False):
    path=Path(path); render_dir=Path(render_dir); render_dir.mkdir(parents=True,exist_ok=True)
    pages=metadata(path); pdf=pdfplumber.open(path) if path.suffix.lower()=='.pdf' else None
    try:
        for index,page in enumerate(pages):
            if cancelled(): raise InterruptedError('Cancelled')
            words=pdf.pages[index].extract_words(x_tolerance=2,y_tolerance=3) if pdf else []
            page['ocr']=False; page['warnings']=[]
            if sum(len(w['text']) for w in words)<20:
                if not ocr_binary or not Path(ocr_binary).is_file():
                    page['warnings'].append(issue('ocr_unavailable','',f'Page {index+1} needs local OCR, which is unavailable in this installation.'))
                else:
                    original=render_dir/f'page-{index+1}-input.png'; upright=render_dir/f'page-{index+1}.png'
                    render_original(path,index+1,original)
                    try:
                        data=json.loads(run_ocr([str(ocr_binary),str(original),'--normalized-output',str(upright)],cancelled))
                        page['width'],page['height']=data['width'],data['height']
                        page['image']=upright.name;page['ocr']=True
                        page['warnings'].append(issue('ocr_review','',f'Page {index+1} was read by local OCR. Compare extracted values with the source before approval; confidence scores cannot guarantee accuracy.','review'))
                        words=[]
                        for observation in data.get('observations',[]):
                            for word in observation.get('words',[]) or [observation]:
                                x0,y0,x1,y1=word['bbox']
                                words.append({'text':word['text'],'x0':x0*page['width'],'top':y0*page['height'],'x1':x1*page['width'],'bottom':y1*page['height'],'confidence':word.get('confidence',observation.get('confidence',0))})
                        if not words: page['warnings'].append(issue('no_text','',f'No readable text found on page {index+1}.'))
                        elif any(w.get('confidence',1)<.8 for w in words): page['warnings'].append(issue('ocr_confidence','',f'Page {index+1} contains low-confidence OCR. Verify the original before approval.','review'))
                    except (subprocess.SubprocessError,ValueError,KeyError,TypeError):
                        page['warnings'].append(issue('ocr_failed','',f'Local OCR failed on page {index+1}; retry or use a clearer scan.'))
            page['words']=words
            page['lines']=[{'text':text(line),'bbox':bbox(line)} for line in lines_for(words)]
            progress(int((index+1)/len(pages)*70))
        return pages
    finally:
        if pdf: pdf.close()

def extract_pages(pages,template):
    validate_template(template)
    result={'fields':{f['name']:'' for f in template['fields']},'items':[],'evidence':{},'issues':[],'text_pdf':True,'ocr':any(p.get('ocr') for p in pages)}
    matched={};table=template['table']; any_text=False
    for page in pages:
        words=page.get('words',[]);any_text|=bool(words);lines=lines_for(words)
        result['issues'].extend(page.get('warnings',[]))
        for field in template['fields']:
            anchor=field['anchor'].strip()
            for index,line in enumerate(lines):
                line_text=text(line)
                if anchor_matches(line_text,anchor):
                    source=line
                    if field.get('position')=='below':
                        if index+1>=len(lines):continue
                        source=lines[index+1];value=text(source)
                    else:value=line_text[len(anchor):].strip()
                    if value:
                        matched.setdefault(field['name'],[]).append(value)
                        result['fields'][field['name']]=value
                        result['evidence'][field['name']]={'page':page['page'],'bbox':bbox(source),'text':text(source),'ocr':page.get('ocr',False)}
        if not table.get('enabled'):continue
        active=False;found=False
        for line in lines:
            value=text(line); lower=value.casefold()
            if anchor_matches(value,table['header_anchor']):active=found=True;continue
            if not active:continue
            if any(anchor_matches(value,v) for v in table['end_anchors']):active=False;continue
            if any(anchor_matches(value,v) for v in table['ignore_prefixes']):continue
            cells={c['name']:text([w for w in line if c['x0']<=((w['x0']+w['x1'])/2)/page['width']<c['x1']]) for c in table['columns']}
            if not any(cells.values()):
                result['issues'].append(issue('unmapped_row','items',f'Text outside the configured columns on page {page["page"]}: {value}'))
                continue
            numeric=[c['name'] for c in table['columns'] if c['type'] in {'number','currency'}]
            text_columns=[c['name'] for c in table['columns'] if c['type']=='text']
            if numeric and not any(cells[n] for n in numeric):
                if result['items'] and result['items'][-1].get('evidence',{}).get('page')==page['page']:
                    for name in text_columns:
                        if cells[name]:result['items'][-1][name]=(result['items'][-1][name]+' '+cells[name]).strip()
                    ev=result['items'][-1]['evidence'];ev['text']+='\n'+value;ev['bbox'][3]=max(ev['bbox'][3],bbox(line)[3])
                else:result['issues'].append(issue('unmapped_row','items',f'Unmapped continuation on page {page["page"]}: {value}'))
                continue
            cells['evidence']={'page':page['page'],'bbox':bbox(line),'text':value,'ocr':page.get('ocr',False)}
            result['items'].append(cells)
        if words and not found:result['issues'].append(issue('table_header_missing','items',f'Table header not found on page {page["page"]}; verify this page was not needed.','review'))
    result['text_pdf']=any_text
    for name,values in matched.items():
        if len(set(values))>1:result['issues'].append(issue('conflicting_field','fields.'+name,f'Multiple different values found for {name}.','review'))
    result['extraction_issues']=json.loads(json.dumps(result['issues']))
    return validate_result(result,template)

def validate_result(result,template):
    issues=json.loads(json.dumps(result.get('extraction_issues',[])))
    def validate_value(obj,field,path):
        value=str(obj.get(field['name'],'')).strip();obj[field['name']]=value
        if not value:
            if field.get('required'):issues.append(issue('required',path,f'{field.get("label",field["name"])} is required.'))
            return
        try:
            kind=field['type']
            if kind in {'number','currency'}:
                number=decimal_value(value)
                if kind=='currency' and number!=number.quantize(Decimal('.01')):raise ValueError('Currency amounts require at most two decimal places; no rounding was applied.')
                obj[field['name']]=format(number,'.2f') if kind=='currency' else format(number,'f')
            elif kind=='date':date.fromisoformat(value)
            elif kind=='email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):raise ValueError('Use a valid email address')
        except (ValueError,InvalidOperation):issues.append(issue('type',path,f'{field.get("label",field["name"])} is not a valid {field["type"]}. Check the source; dates use YYYY-MM-DD and decimals use a dot.'))
    for field in template['fields']:validate_value(result['fields'],field,'fields.'+field['name'])
    rows=result.get('items',[])
    if template['table'].get('enabled') and not rows:issues.append(issue('empty_table','items','No line items extracted. Review the table geometry and source.'))
    for i,row in enumerate(rows):
        for col in template['table'].get('columns',[]):validate_value(row,col,f'items.{i}.{col["name"]}')
    validation=template.get('validation',{})
    if validation.get('enabled'):
        q,p,l=(validation.get(k) for k in ['quantity','unit_price','line_total'])
        subtotal,tax,total=(validation.get(k) for k in ['subtotal','tax','total'])
        if q and p and l:
            for index,row in enumerate(rows):
                try:
                    expected=(decimal_value(row[q])*decimal_value(row[p])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                    if expected!=decimal_value(row[l]):issues.append(issue('line_mismatch',f'items.{index}',f'Row {index+1}: quantity × unit price is {expected}, different from its line total.'))
                except (ValueError,InvalidOperation,KeyError):pass
        if l and subtotal and rows:
            try:
                expected=sum((decimal_value(row[l]) for row in rows),Decimal(0))
                if expected!=decimal_value(result['fields'][subtotal]):issues.append(issue('subtotal_mismatch','fields.'+subtotal,f'Line items add to {expected}, different from the subtotal.'))
            except (ValueError,InvalidOperation,KeyError):pass
        if subtotal and tax and total:
            try:
                if decimal_value(result['fields'][subtotal])+decimal_value(result['fields'][tax])!=decimal_value(result['fields'][total]):issues.append(issue('total_mismatch','fields.'+total,'Subtotal + tax does not equal the total.'))
            except (ValueError,InvalidOperation,KeyError):pass
    result['issues']=issues;result['status']='needs_review' if issues else 'validated'
    return result
