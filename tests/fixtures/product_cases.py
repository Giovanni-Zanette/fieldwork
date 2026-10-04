from pathlib import Path
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pagesizes import A4
import json,hashlib
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent;ROOT.mkdir(exist_ok=True)
W,H=A4

def doc(name,title,fields,rows,columns,footers,multipage=False):
 c=Canvas(str(ROOT/name),pagesize=A4,pageCompression=0)
 def page_header(page):
  c.setFont('Helvetica-Bold',20);c.drawString(40,H-45,title)
  c.setFont('Helvetica',10);c.drawString(40,H-64,'FICTIONAL QA DOCUMENT — NOT FOR COMMERCIAL USE')
  y=H-105
  for k,v in fields:c.drawString(40,y,k+': '+v);y-=19
  y-=20;c.setFont('Helvetica-Bold',10)
  for text,x in columns:c.drawString(x,y,text)
  return y-25
 y=page_header(1)
 for i,row in enumerate(rows):
  if multipage and i==2:
   c.setFont('Helvetica',9);c.drawString(40,45,'Footer: continuation follows');c.showPage();y=page_header(2)
  c.setFont('Helvetica',11)
  for value,(_,x) in zip(row,columns):c.drawString(x,y,str(value))
  y-=25
 c.setFont('Helvetica',11)
 for k,v in footers:y-=22;c.drawString(360,y,k+': '+v)
 c.setFont('Helvetica',9);c.drawString(40,45,'Footer: fictional test document');c.save()

invoice_fields=[('Vendor','Juniper Studio Test'),('Invoice ID','INV-QA-732'),('Invoice date','2026-10-04'),('Customer email','buyer@example.invalid')]
invoice_rows=[['Design audit','2','125.50','251.00'],['Research pack','3','42.25','126.75']]
doc('invoice-unseen.pdf','INVOICE',invoice_fields,invoice_rows,[('Description',40),('Qty',340),('Unit price',410),('Line total',500)],[('Subtotal','377.75'),('Tax','56.66'),('Total','434.41')])
doc('price-list-unseen.pdf','AUTUMN PRICE LIST',[('Supplier','Oak Supply Test'),('Effective date','2026-10-04')],[['SKU-Q71','Amber notebook','23.75'],['SKU-Q72','Graph paper pack','49.90']],[('SKU',40),('Description',190),('Price',470)],[])
doc('custom-form-unseen.pdf','SERVICE REQUEST',[('Reference','SR-QA-913'),('Contact','Mara Fiction'),('Email','mara@example.invalid'),('Appointment','2026-11-05')],[],[],[])
doc('multipage-unseen.pdf','PURCHASE ORDER',[('Supplier','Maple Test'),('Order number','MP-QA-819'),('Order date','2026-10-04')],[['Index cards','2','10.00','20.00'],['Clip pack','4','5.00','20.00'],['Desk pad','1','30.00','30.00'],['Folder set','3','10.00','30.00']],[('Description',40),('Qty',340),('Unit price',410),('Line total',500)],[('Subtotal','100.00'),('Tax','15.00'),('Total','115.00')],multipage=True)
manifest={'created':datetime.now(timezone.utc).isoformat(),'purpose':'Independent product acceptance; all synthetic; no invoice/order source values supplied to app builder before extraction.', 'expected':{'invoice-unseen.pdf':{'fields':{'vendor':'Juniper Studio Test','invoice_id':'INV-QA-732','date':'2026-10-04','email':'buyer@example.invalid','subtotal':'377.75','tax':'56.66','total':'434.41'},'items':[dict(zip(('description','quantity','unit_price','line_total'),r)) for r in invoice_rows]},'price-list-unseen.pdf':{'fields':{'supplier':'Oak Supply Test','effective':'2026-10-04'},'items':[{'sku':'SKU-Q71','description':'Amber notebook','price':'23.75'},{'sku':'SKU-Q72','description':'Graph paper pack','price':'49.90'}]},'custom-form-unseen.pdf':{'fields':{'reference':'SR-QA-913','contact':'Mara Fiction','email':'mara@example.invalid','appointment':'2026-11-05'},'items':[]},'multipage-unseen.pdf':{'fields':{'supplier':'Maple Test','order_number':'MP-QA-819','order_date':'2026-10-04','subtotal':'100.00','tax':'15.00','total':'115.00'},'items':[{'description':a,'quantity':b,'unit_price':c,'line_total':d} for a,b,c,d in [['Index cards','2','10.00','20.00'],['Clip pack','4','5.00','20.00'],['Desk pad','1','30.00','30.00'],['Folder set','3','10.00','30.00']]]}}}
manifest['sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.pdf')}
(ROOT/'expected.json').write_text(json.dumps(manifest,indent=2)+'\n')

# Optional regeneration uses source-build-only reportlab plus runtime PDFium/Pillow.
import pypdfium2 as pdfium
from PIL import ImageFilter
pdf=pdfium.PdfDocument(ROOT/'invoice-unseen.pdf')
im=pdf[0].render(scale=2.5).to_pil()
im.rotate(90,expand=True).save(ROOT/'invoice-scan-rotated.png')
im.resize((300,425)).filter(ImageFilter.GaussianBlur(1.5)).save(ROOT/'invoice-scan-weak.png')
pdf.close()
