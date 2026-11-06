"""Task 1: a strict MCP server using the official Python SDK."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

from mcp import MCPError
from mcp.server import MCPServer
from mcp.types import INVALID_PARAMS
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator


# Explicitly bind logs to stderr. Never use print() in a stdio MCP server.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("customer-mcp")

mcp = MCPServer(
    "customer-operations",
    version="1.0.0",
    instructions="Read customer records and submit validated refund requests.",
)

CUSTOMER_ID_PATTERN = re.compile(r"^CUST-[A-Z0-9]{5}$")


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CustomerRecordInput(StrictInput):
    customer_id: StrictStr

    @field_validator("customer_id")
    @classmethod
    def valid_customer_id(cls, value: str) -> str:
        if not CUSTOMER_ID_PATTERN.fullmatch(value):
            raise ValueError("customer_id must match CUST-XXXXX")
        return value


class RefundInput(CustomerRecordInput):
    amount: float = Field(gt=0, allow_inf_nan=False)
    reason: StrictStr = Field(min_length=10, max_length=500)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("reason must contain at least 10 non-whitespace characters")
        return value.strip()


CUSTOMERS: dict[str, dict[str, Any]] = {
    "CUST-A1B2C": {
        "customer_id": "CUST-A1B2C",
        "name": "Avery Chen",
        "status": "active",
        "email": "avery@example.com",
    },
    "CUST-00001": {
        "customer_id": "CUST-00001",
        "name": "Jordan Rivera",
        "status": "active",
        "email": "jordan@example.com",
    },
}


def _invalid_params(error: ValidationError) -> MCPError:
    details = [
        {
            "field": ".".join(str(part) for part in item["loc"]),
            "message": item["msg"],
            "type": item["type"],
        }
        for item in error.errors(include_url=False, include_input=False)
    ]
    return MCPError(code=INVALID_PARAMS, message="Invalid tool arguments", data=details)


def validate(model: type[StrictInput], **values: object) -> StrictInput:
    try:
        return model.model_validate(values)
    except ValidationError as exc:
        raise _invalid_params(exc) from None


@mcp.tool()
def get_customer_record(customer_id: str) -> dict[str, Any]:
    """Return one customer record for an ID formatted as CUST-XXXXX."""
    request = validate(CustomerRecordInput, customer_id=customer_id)
    assert isinstance(request, CustomerRecordInput)
    record = CUSTOMERS.get(request.customer_id)
    if record is None:
        # A missing record is deliberately represented as a successful, stable
        # lookup result rather than exposing storage-layer details.
        return {"found": False, "customer_id": request.customer_id}
    return {"found": True, "record": record}


@mcp.tool()
def trigger_refund(customer_id: str, amount: float, reason: str) -> dict[str, Any]:
    """Submit a refund with a positive amount and a meaningful reason."""
    request = validate(
        RefundInput,
        customer_id=customer_id,
        amount=amount,
        reason=reason,
    )
    assert isinstance(request, RefundInput)
    refund_id = f"RF-{request.customer_id.removeprefix('CUST-')}"
    logger.info("Refund accepted id=%s customer=%s", refund_id, request.customer_id)
    return {
        "accepted": True,
        "refund_id": refund_id,
        "customer_id": request.customer_id,
        "amount": request.amount,
        "reason": request.reason,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")

