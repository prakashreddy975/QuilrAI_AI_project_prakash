import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import StreamingPIIRedactor, luhn_valid


def stream(redactor, chunks):
    return "".join(redactor.feed(chunk) for chunk in chunks) + redactor.flush()


class RedactorTests(unittest.TestCase):
    def test_email_across_chunks(self):
        result = stream(StreamingPIIRedactor(32), ["Contact al", "ice@example", ".com today"])
        self.assertEqual(result, "Contact [REDACTED] today")

    def test_ssn_across_chunks(self):
        result = stream(StreamingPIIRedactor(32), ["SSN 123-", "45-6789 is private"])
        self.assertEqual(result, "SSN [REDACTED] is private")

    def test_valid_card_redacted(self):
        result = stream(StreamingPIIRedactor(32), ["Card 4111 1111 ", "1111 1111."])
        self.assertEqual(result, "Card [REDACTED].")

    def test_non_card_number_preserved(self):
        result = stream(StreamingPIIRedactor(32), ["Reference 4111111111111112."])
        self.assertEqual(result, "Reference 4111111111111112.")

    def test_luhn(self):
        self.assertTrue(luhn_valid("4111-1111-1111-1111"))
        self.assertFalse(luhn_valid("4111-1111-1111-1112"))


if __name__ == "__main__":
    unittest.main()

