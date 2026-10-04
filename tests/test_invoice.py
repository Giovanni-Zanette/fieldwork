"""Independent invoice acceptance with real, wholly fictional PDF fixtures."""
import copy
from pathlib import Path
import tempfile
import unittest

from backend.engine import INVOICE_TEMPLATE, extract_pages, read_pages, validate_result, validate_template

ROOT = Path(__file__).resolve().parent / 'fixtures'
ITEMS = [
    {'quantity':'2', 'description':'Cable pack woven shield, assorted colours', 'unit_price':'120.00', 'line_total':'240.00'},
    {'quantity':'3', 'description':'Label roll', 'unit_price':'30.00', 'line_total':'90.00'},
]


class InvoiceAcceptance(unittest.TestCase):
    def extract(self, filename='invoice-columns.pdf', *, date_format=None):
        template=copy.deepcopy(INVOICE_TEMPLATE)
        if date_format:
            next(f for f in template['fields'] if f['name']=='invoice_date')['date_format']=date_format
        validate_template(template)
        with tempfile.TemporaryDirectory() as temporary:
            result=extract_pages(read_pages(ROOT/filename,temporary),template)
        return result,template

    def assert_rows(self,result):
        self.assertEqual([{key:row.get(key) for key in ITEMS[0]} for row in result['items']],ITEMS)
        for row in result['items']:
            self.assertEqual(row['evidence']['page'],1)
            self.assertTrue(row['evidence']['text'])

    def test_side_by_side_labels_wrapping_and_paid_total(self):
        result,_=self.extract()
        self.assertEqual(result['fields']['bill_to'],'Lyra Example Ltd')
        self.assertEqual(result['fields']['invoice_number'],'FW-204')
        self.assertEqual(result['fields']['invoice_date'],'2026-06-24')
        self.assertEqual(result['fields']['subtotal'],'330.00')
        self.assertEqual(result['fields']['tax'],'49.50')
        self.assertEqual(result['fields']['total'],'379.50')
        self.assertEqual(result['fields']['balance_due'],'179.50')
        self.assertEqual(result['status'],'validated',result['issues'])
        self.assert_rows(result)
        self.assertNotIn('Invoice',result['evidence']['bill_to']['text'])
        self.assertNotIn('Date:',result['evidence']['bill_to']['text'])

    def test_headers_follow_swapped_columns_and_wider_page(self):
        result,_=self.extract('invoice-columns-swapped.pdf')
        self.assert_rows(result)
        self.assertEqual(result['status'],'validated',result['issues'])

    def test_missing_tax_and_total_are_not_invented_from_balance(self):
        result,_=self.extract('invoice-no-tax-total.pdf')
        self.assertEqual(result['fields']['tax'],'')
        self.assertEqual(result['fields']['total'],'')
        self.assertEqual(result['fields']['balance_due'],'330.00')
        self.assert_rows(result)

    def test_ambiguous_numeric_date_requires_review(self):
        result,_=self.extract('invoice-date-ambiguous.pdf')
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['fields']['invoice_date'],'6/7/26')
        self.assertTrue(any(i['path']=='fields.invoice_date' for i in result['issues']),result['issues'])

    def test_explicit_date_order_resolves_ambiguity(self):
        for order,expected in [('mdy','2026-06-07'),('dmy','2026-07-06')]:
            with self.subTest(order=order):
                result,_=self.extract('invoice-date-ambiguous.pdf',date_format=order)
                self.assertEqual(result['fields']['invoice_date'],expected)
                self.assertEqual(result['status'],'validated',result['issues'])

    def test_missing_required_table_header_fails_closed(self):
        result,_=self.extract('invoice-header-missing.pdf')
        self.assertEqual(result['status'],'needs_review')
        self.assertTrue(result['issues'])

    def test_invalid_calendar_date_is_not_coerced(self):
        result,template=self.extract()
        result['fields']['invoice_date']='2026-02-30'
        result=validate_result(result,template)
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['fields']['invoice_date'],'2026-02-30')

    def test_vat_registration_is_not_tax(self):
        result,_=self.extract('invoice-vat-registration.pdf')
        self.assertEqual(result['fields']['tax'],'')
        self.assertEqual(result['status'],'validated',result['issues'])

    def test_total_inside_description_does_not_end_table(self):
        result,_=self.extract('invoice-total-description.pdf')
        expected=copy.deepcopy(ITEMS)
        expected[0]['description']='Total maintenance Total coverage, annual support'
        self.assertEqual([{key:row.get(key) for key in expected[0]} for row in result['items']],expected)
        self.assertEqual(result['fields']['total'],'379.50')
        self.assertEqual(result['status'],'validated',result['issues'])

    def test_literal_currency_text_in_description_is_preserved(self):
        result,_=self.extract('invoice-currency-description.pdf')
        self.assertEqual(result['items'][0]['description'],'Cable pack R USD')
        self.assertEqual(len(result['items']),2)
        self.assertEqual(result['status'],'validated',result['issues'])


if __name__ == '__main__': unittest.main()
