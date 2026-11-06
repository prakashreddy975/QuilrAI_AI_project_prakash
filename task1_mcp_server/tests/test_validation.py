import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError
from server import CustomerRecordInput, RefundInput


class ValidationTests(unittest.TestCase):
    def test_customer_id_accepts_exact_format(self):
        value = CustomerRecordInput(customer_id="CUST-A1B2C")
        self.assertEqual(value.customer_id, "CUST-A1B2C")

    def test_customer_id_rejects_bad_format(self):
        with self.assertRaises(ValidationError):
            CustomerRecordInput(customer_id="cust-12345")

    def test_refund_rejects_non_positive_amount(self):
        with self.assertRaises(ValidationError):
            RefundInput(customer_id="CUST-12345", amount=0, reason="Valid refund reason")

    def test_refund_rejects_whitespace_reason(self):
        with self.assertRaises(ValidationError):
            RefundInput(customer_id="CUST-12345", amount=1.0, reason="            ")


if __name__ == "__main__":
    unittest.main()

