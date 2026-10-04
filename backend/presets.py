"""Opt-in deterministic presets; no document-specific values or network services."""
from datetime import date
import re


INVOICE_TEMPLATE = {
    'name':'Invoices',
    'fields':[
        {'name':'bill_to','label':'Bill to','type':'text','required':True,'anchor':'Bill To:','aliases':['Bill to','Billed to','Customer:'],'position':'after','fallback_below':True,'match_mode':'inline'},
        {'name':'invoice_number','label':'Invoice number','type':'text','required':True,'anchor':'Invoice no.','aliases':['Invoice number','Invoice #','Invoice No:'],'position':'after','match_mode':'inline'},
        {'name':'invoice_date','label':'Invoice date','type':'date','required':True,'anchor':'Invoice date','aliases':['Date'],'position':'after','match_mode':'inline','date_format':'auto','two_digit_year_century':2000},
        {'name':'subtotal','label':'Subtotal','type':'currency','required':True,'anchor':'Subtotal','aliases':['Sub total'],'position':'after','match_mode':'inline'},
        {'name':'tax','label':'Tax','type':'currency','required':False,'anchor':'Tax','aliases':['VAT','Sales tax'],'position':'after','match_mode':'inline'},
        {'name':'total','label':'Invoice total','type':'currency','required':False,'anchor':'Invoice total','aliases':['Grand total','Total'],'position':'after','match_mode':'inline'},
        {'name':'balance_due','label':'Balance due','type':'currency','required':False,'anchor':'Balance due','aliases':['Amount due'],'position':'after','match_mode':'inline'},
    ],
    'table':{'enabled':True,'mode':'header','header_anchor':'Quantity','end_anchors':['Subtotal','Sub total','Tax','VAT','Grand total','Invoice total','Total','Balance due','Amount due','Credit','Discount','Payment','Paid','Deposit'],'ignore_prefixes':[],
             'columns':[
                 {'name':'quantity','label':'Quantity','type':'number','required':True,'aliases':['Quantity','Qty'],'x0':.05,'x1':.24},
                 {'name':'description','label':'Description','type':'text','required':True,'aliases':['Description','Item description','Details'],'x0':.24,'x1':.52},
                 {'name':'unit_price','label':'Unit price','type':'currency','required':True,'aliases':['Unit price','Unit cost','Rate','Price'],'x0':.52,'x1':.73},
                 {'name':'line_total','label':'Amount','type':'currency','required':True,'aliases':['Amount','Line total','Total'],'x0':.73,'x1':.97},
             ]},
    'validation':{'enabled':True,'quantity':'quantity','unit_price':'unit_price','line_total':'line_total','subtotal':'subtotal','tax':'tax','total':'total'},
    'duplicate_fields':['bill_to','invoice_number'],'export_columns':[],
}


class AmbiguousDate(ValueError):
    pass


def normalize_date(value,field):
    value=value.strip()
    try:return date.fromisoformat(value).isoformat()
    except ValueError:pass
    mode=field.get('date_format','iso')
    if mode=='iso':raise ValueError('Use YYYY-MM-DD')
    match=re.fullmatch(r'(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})',value)
    if not match:raise ValueError('Use a numeric date with a supported format')
    a,b,year=match.groups();a=int(a);b=int(b)
    if len(year)==2:
        century=field.get('two_digit_year_century')
        if century not in {1900,2000}:raise AmbiguousDate('Use a four-digit year or choose a template century')
        year=century+int(year)
    else:year=int(year)
    if mode=='mdy':month,day=a,b
    elif mode=='dmy':day,month=a,b
    elif a>12 and b<=12:day,month=a,b
    elif b>12 and a<=12:month,day=a,b
    elif a==b:month,day=a,b
    else:raise AmbiguousDate('Choose day/month or month/day in this template, or enter YYYY-MM-DD after checking the source')
    return date(year,month,day).isoformat()
