import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import TokenAuthenticator, parse_rpc


class GatewayTests(unittest.TestCase):
    def test_authentication_extracts_role(self):
        auth = TokenAuthenticator({"secret": "admin"})
        self.assertEqual(auth.authenticate("Bearer secret").role, "admin")

    def test_bad_scheme_is_rejected(self):
        auth = TokenAuthenticator({"secret": "viewer"})
        self.assertIsNone(auth.authenticate("Basic secret"))

    def test_invalid_json_rpc_version(self):
        parsed, error = parse_rpc({"jsonrpc": "1.0", "id": 1, "method": "tools/list"})
        self.assertIsNone(parsed)
        self.assertEqual(error.status_code, 200)

    def test_valid_tools_call(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "x"}}
        parsed, error = parse_rpc(body)
        self.assertIsNone(error)
        self.assertEqual(parsed, body)


if __name__ == "__main__":
    unittest.main()

