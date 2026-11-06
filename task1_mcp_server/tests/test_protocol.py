import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client, MCPError
from mcp.types import INVALID_PARAMS
from server import mcp


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_registered_and_callable(self):
        async with Client(mcp) as client:
            tools = await client.list_tools()
            self.assertEqual(
                {tool.name for tool in tools.tools},
                {"get_customer_record", "trigger_refund"},
            )
            result = await client.call_tool(
                "get_customer_record",
                {"customer_id": "CUST-A1B2C"},
            )
            self.assertTrue(result.structured_content["found"])

    async def test_bad_customer_id_is_invalid_params(self):
        async with Client(mcp) as client:
            with self.assertRaises(MCPError) as caught:
                await client.call_tool(
                    "get_customer_record",
                    {"customer_id": "not-an-id"},
                )
            self.assertEqual(caught.exception.code, INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()

