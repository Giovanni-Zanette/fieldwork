"""Generate fictional invoice geometry regressions; no customer content is used."""
from pathlib import Path
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent


def make(name, *, swapped=False, date='6/24/26', tax=True, total=True, missing_header=False, description_total=False, vat_registration=False, description_currency=False):
    width, height = (760 if swapped else 612), 792
    c = canvas.Canvas(str(ROOT / name), pagesize=(width, height))
    c.setTitle('Synthetic invoice acceptance fixture')
    def text(x, y, value, bold=False, size=11):
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        c.drawString(x, height-y, value)
    text(40, 48, 'LYRA / FICTIONAL INVOICE', True, 18)
    text(40, 76, 'SYNTHETIC TEST DATA - NOT A REAL COMMERCIAL DOCUMENT', size=9)
    text(40, 128, 'Bill to:', True)
    text(40, 152, 'Lyra Example Ltd')
    text(40, 176, '17 Demo Lane')
    text(40, 200, 'Fableton 0000')
    if vat_registration: text(40, 224, 'VAT number: 4123456789')
    text(width-205, 152, 'Date: '+date)
    text(width-205, 176, 'Invoice #: FW-204')
    positions = ({'description': 45, 'quantity': 355, 'line_total': 455, 'unit_price': 625}
                 if swapped else {'quantity': 42, 'description': 112, 'unit_price': 365, 'line_total': 515})
    labels = {'quantity': 'Qty', 'description': 'Description', 'unit_price': 'Unit Price', 'line_total': 'Amount'}
    for key, x in positions.items():
        if not (missing_header and key == 'quantity'):
            text(x, 264, labels[key], True)
    def row(y, quantity, description, price, amount):
        for key, value in [('quantity',quantity),('description',description),('unit_price',price),('line_total',amount)]:
            x=positions[key]
            if key in {'unit_price','line_total'}:
                text(x, y, 'R')
                text(x+20, y, value)
            else: text(x, y, value)
    row(294, '2', 'Total maintenance' if description_total else 'Cable pack', '120.00', '240.00')
    text(positions['description'], 312, 'R USD' if description_currency else ('Total coverage, annual support' if description_total else 'woven shield, assorted colours'))
    row(342, '3', 'Label roll', '30.00', '90.00')
    # An empty printed form row has currency symbols but no values.
    row(370, '', '', '', '')
    text(width-200, 430, 'Subtotal: R 330.00')
    if tax: text(width-200, 454, 'Tax: R 49.50')
    if total: text(width-200, 478, 'Total: R 379.50')
    text(width-200, 502, 'Credit/discount: R 200.00' if total else 'Credit/discount: -')
    text(width-200, 526, 'Balance due: R '+('179.50' if total else '330.00'))
    c.save()


if __name__ == '__main__':
    make('invoice-columns.pdf')
    make('invoice-columns-swapped.pdf', swapped=True)
    make('invoice-date-ambiguous.pdf', date='6/7/26')
    make('invoice-no-tax-total.pdf', tax=False, total=False)
    make('invoice-header-missing.pdf', missing_header=True)
    make('invoice-vat-registration.pdf', tax=False, total=False, vat_registration=True)
    make('invoice-total-description.pdf', description_total=True)
    make('invoice-currency-description.pdf', description_currency=True)
