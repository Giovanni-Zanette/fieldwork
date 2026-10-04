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
from .presets import INVOICE_TEMPLATE, normalize_date, AmbiguousDate

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
            aliases=f.get('aliases',[])
            if not isinstance(aliases,list) or len(aliases)>15 or any(not isinstance(a,str) or not 1<=len(a.strip())<=150 for a in aliases):raise ValueError('Use up to 15 nonempty label aliases')
            if f.get('match_mode','line_start') not in {'line_start','inline'}:raise ValueError('Unsupported label matching mode')
            if f.get('date_format','iso') not in {'iso','auto','dmy','mdy'}:raise ValueError('Unsupported date format')
            if f.get('two_digit_year_century') not in {None,1900,2000}:raise ValueError('Two-digit years require a configured century')
            if not isinstance(f.get('fallback_below',False),bool):raise ValueError('Invalid below-label fallback')
        return names
    field_names = definitions(fields)
    table = template.get('table', {'enabled':False,'columns':[]})
    template['table'] = table
    column_names = set()
    if table.get('mode','fixed') not in {'fixed','header'}:raise ValueError('Unsupported table layout mode')
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

def label_spans(line,aliases):
    """Return exact whole-token label spans, longest labels first at each position."""
    value=text(line);offsets=[];offset=0
    for word in line:
        offsets.append((offset,offset+len(word['text'])));offset+=len(word['text'])+1
    found=[]
    for alias in sorted(set(aliases),key=len,reverse=True):
        clean=alias.strip().rstrip(':').strip()
        pattern=r'(?<!\w)'+re.escape(clean).replace(r'\ ',r'\s+')+r'(?!\w)\s*:?'
        for match in re.finditer(pattern,value,re.I):
            indexes=[i for i,(a,b) in enumerate(offsets) if b>match.start() and a<match.end()]
            if not indexes:continue
            first,last=indexes[0],indexes[-1]
            # Matching starts/ends within a larger token is not a label match.
            if match.start()!=offsets[first][0]:continue
            suffix=value[match.end():offsets[last][1]]
            if suffix and suffix not in {':','.'}:continue
            if clean.casefold()=='date' and first and line[first-1]['text'].rstrip(':').casefold() in {'due','delivery','payment','shipping','ship'}:continue
            if clean.casefold() in {'vat','tax'} and last+1<len(line) and re.sub(r'[^a-z]','',line[last+1]['text'].casefold()) in {'no','nr','number','registration','reg','id'}:continue
            if any(not(last<a or first>b) for a,b in found):continue
            found.append((first,last))
    return sorted(found)


def inline_value(lines,index,span,field,fields,page):
    line=lines[index];first,last=span
    stop=len(line)
    for other in fields:
        for a,b in label_spans(line,[other['anchor']]+other.get('aliases',[])):
            if a>last:stop=min(stop,a)
    source=line[last+1:stop]
    if field.get('position')=='below' or not source and field.get('fallback_below'):
        left=line[first]['x0'];right=page['width'] if left>=page['width']*.5 else page['width']*.5
        source=[]
        for candidate in lines[index+1:]:
            if candidate[0]['top']-line[0]['top']>60:break
            selected=[w for w in candidate if w['x0']>=left-page['width']*.02 and (w['x0']+w['x1'])/2<right]
            if selected:source=selected;break
    # A printed dash is absence, not a numeric zero.
    value=text(source).strip().lstrip(':').strip()
    if field['type'] in {'currency','number'} and re.fullmatch(r'(?:R|ZAR|USD|EUR|GBP|[$€£])?\s*[-–—]*',value,re.I):value=''
    return value,source


def inferred_columns(line,table,width):
    matches=[]
    for col in table['columns']:
        spans=label_spans(line,col.get('aliases') or [col.get('label',col['name'])])
        if len(spans)!=1:return None
        a,b=spans[0];matches.append((line[a]['x0'],line[b]['x1'],col))
    matches.sort(key=lambda item:item[0])
    if any(a[1]>b[0] for a,b in zip(matches,matches[1:])):return None
    result=[]
    for index,(left,right,col) in enumerate(matches):
        # The next header's left edge gives text room to wrap; numeric columns often
        # align values to the right of their heading, so avoid midpoint truncation.
        start=0 if index==0 else (matches[index-1][1]+left)/2
        end=width if index==len(matches)-1 else (right+matches[index+1][0])/2
        result.append({**col,'x0':start/width,'x1':end/width})
    return result


def empty_invoice_row(cells,columns):
    for column in columns:
        value=cells[column['name']]
        if column['type'] not in {'number','currency'}:
            if value.strip():return False
        elif re.sub(r'(?:ZAR|USD|EUR|GBP|R|[$€£]|[-–—]|\s)','',value,flags=re.I):return False
    return True


def summary_line(line,table,columns,width):
    cells={c['name']:text([w for w in line if c['x0']<=((w['x0']+w['x1'])/2)/width<c['x1']]) for c in columns}
    numeric=[c for c in columns if c['type'] in {'number','currency'}]
    quantity=next((c for c in numeric if c['type']=='number'),None)
    if quantity:
        try:decimal_value(cells[quantity['name']]);return False
        except ValueError:pass
    for alias in table['end_anchors']:
        for a,b in label_spans(line,[alias]):
            # Summaries may be positioned independently of the table's columns.
            # Require an amount directly after the whole label, not a word such
            # as "Total" appearing inside an item description.
            try:decimal_value(text(line[b+1:]).lstrip(':').strip())
            except ValueError:continue
            centre=(line[a]['x0']+line[b]['x1'])/2/width
            col=next((c for c in columns if c['x0']<=centre<c['x1']),None)
            if col and col['type']=='text' and cells[col['name']].strip().rstrip(':').casefold()!=text(line[a:b+1]).strip().rstrip(':').casefold():continue
            return True
    return False


def invoice_body_indexes(lines,table,width):
    blocked=set();active=False;columns=[]
    for index,line in enumerate(lines):
        inferred=inferred_columns(line,table,width)
        if inferred:columns=inferred;active=True;blocked.add(index);continue
        if not active:continue
        if summary_line(line,table,columns,width):active=False
        else:blocked.add(index)
    return blocked


def invoice_table(lines,table,page,result):
    """Group wrapped descriptions around numeric row baselines, not text-line order."""
    active=False;found=False;columns=[];block=[]
    def flush():
        if not block:return
        numeric=[c['name'] for c in columns if c['type'] in {'number','currency'}]
        texts=[c['name'] for c in columns if c['type']=='text']
        mapped=[];anchors=[]
        for line in block:
            cells={c['name']:text([w for w in line if c['x0']<=((w['x0']+w['x1'])/2)/page['width']<c['x1']]) for c in columns}
            if empty_invoice_row(cells,columns):continue
            entry={'line':line,'cells':cells,'y':sum((w['top']+w['bottom'])/2 for w in line)/len(line)}
            mapped.append(entry)
            if any(any(ch.isdigit() for ch in cells[n]) for n in numeric):anchors.append(entry)
        assigned={id(anchor):[anchor] for anchor in anchors}
        for entry in mapped:
            if any(entry is a for a in anchors):continue
            distances=sorted((abs(entry['y']-a['y']),i,a) for i,a in enumerate(anchors))
            if not distances or distances[0][0]>45 or len(distances)>1 and abs(distances[0][0]-distances[1][0])<1:
                result['issues'].append(issue('unmapped_row','items',f'A table line on page {page["page"]} could not be assigned safely. Review the source and add the missing row or use fixed columns.'))
                continue
            if any(re.sub(r'(?:ZAR|USD|EUR|GBP|R|[$€£]|[-–—]|\s)','',entry['cells'][n],flags=re.I) for n in numeric):
                result['issues'].append(issue('unmapped_row','items',f'Text falls inside an amount column on page {page["page"]}. Review the detected columns.'))
            assigned[id(distances[0][2])].append(entry)
        for anchor in anchors:
            entries=sorted(assigned[id(anchor)],key=lambda e:e['y']);cells=dict(anchor['cells'])
            for name in texts:cells[name]=' '.join(e['cells'][name] for e in entries if e['cells'][name]).strip()
            words=[w for e in entries for w in e['line']]
            cells['evidence']={'page':page['page'],'bbox':bbox(words),'text':'\n'.join(text(e['line']) for e in entries),'ocr':page.get('ocr',False)}
            result['items'].append(cells)
        block.clear()
    for line in lines:
        inferred=inferred_columns(line,table,page['width'])
        if inferred:
            flush();columns=inferred;active=found=True
            result.setdefault('detected_columns',[]).append({'page':page['page'],'columns':[{k:c[k] for k in ['name','x0','x1']} for c in columns]})
            continue
        if not active:continue
        if summary_line(line,table,columns,page['width']):flush();active=False;continue
        if any(anchor_matches(text(line),v) for v in table['ignore_prefixes']):continue
        block.append(line)
    flush()
    if not found:result['issues'].append(issue('table_header_missing','items',f'Invoice column headings were not found completely on page {page["page"]}. Check Quantity, Description, Unit price and Amount aliases, or choose fixed columns.','review'))


def extract_pages(pages,template):
    validate_template(template)
    result={'fields':{f['name']:'' for f in template['fields']},'items':[],'evidence':{},'issues':[],'text_pdf':True,'ocr':any(p.get('ocr') for p in pages)}
    matched={};table=template['table']; any_text=False
    for page in pages:
        words=page.get('words',[]);any_text|=bool(words);lines=lines_for(words)
        table_body=invoice_body_indexes(lines,table,page['width']) if table.get('enabled') and table.get('mode')=='header' else set()
        result['issues'].extend(page.get('warnings',[]))
        for field in template['fields']:
            anchor=field['anchor'].strip()
            for index,line in enumerate(lines):
                line_text=text(line)
                if field.get('match_mode')=='inline':
                    if index in table_body:continue
                    for span in label_spans(line,[anchor]+field.get('aliases',[])):
                        value,source=inline_value(lines,index,span,field,template['fields'],page)
                        if value and source:
                            matched.setdefault(field['name'],[]).append(value)
                            result['fields'][field['name']]=value
                            result['evidence'][field['name']]={'page':page['page'],'bbox':bbox(source),'text':text(source),'ocr':page.get('ocr',False)}
                    continue
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
        if table.get('mode')=='header':
            invoice_table(lines,table,page,result)
            continue
        active=False;found=False;columns=table['columns']
        for line in lines:
            value=text(line)
            if anchor_matches(value,table['header_anchor']):active=found=True;continue
            if not active:continue
            if any(anchor_matches(value,v) for v in table['end_anchors']):active=False;continue
            if any(anchor_matches(value,v) for v in table['ignore_prefixes']):continue
            cells={c['name']:text([w for w in line if c['x0']<=((w['x0']+w['x1'])/2)/page['width']<c['x1']]) for c in columns}
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
        if len(set(values))>1:
            result['issues'].append(issue('conflicting_field','fields.'+name,f'Multiple different values found for {name}.','review'))
            if next(f for f in template['fields'] if f['name']==name).get('match_mode')=='inline':result['fields'][name]=''
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
            elif kind=='date':
                normalized=normalize_date(value,field)
                if normalized!=value:result.setdefault('normalizations',{})[path]={'source':value,'normalized':normalized,'rule':field.get('date_format','iso')}
                obj[field['name']]=normalized
            elif kind=='email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):raise ValueError('Use a valid email address')
        except AmbiguousDate as exc:issues.append(issue('date_ambiguous',path,f'{field.get("label",field["name"])} needs a date format: {exc}.'))
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
