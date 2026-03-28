"""
Payment Tool Handlers

Mock implementations of payment processing tools.
Returns realistic-looking mock data for testing.
"""

import json
import random
import string
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Add base module to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from base.protocol import MCPToolCallResult


def generate_id(prefix: str, length: int = 24) -> str:
    """Generate a Stripe-like ID"""
    chars = string.ascii_letters + string.digits
    random_part = "".join(random.choices(chars, k=length))
    return f"{prefix}_{random_part}"


class PaymentHandlers:
    """Handler implementations for payment tools"""

    @staticmethod
    async def create_payment_intent(arguments: dict) -> MCPToolCallResult:
        """Create a new payment intent"""
        amount = arguments.get("amount", 0)
        currency = arguments.get("currency", "usd")
        customer_id = arguments.get("customer_id")
        description = arguments.get("description", "")

        payment_intent_id = generate_id("pi")

        result = {
            "id": payment_intent_id,
            "object": "payment_intent",
            "amount": amount,
            "currency": currency,
            "status": "requires_confirmation",
            "customer": customer_id,
            "description": description,
            "created": int(datetime.now().timestamp()),
            "client_secret": f"{payment_intent_id}_secret_{generate_id('', 16)}",
            "payment_method_types": ["card"],
        }

        return MCPToolCallResult.json_result(result)

    @staticmethod
    async def confirm_payment(arguments: dict) -> MCPToolCallResult:
        """Confirm and process a payment"""
        payment_intent_id = arguments.get("payment_intent_id", "")
        payment_method_id = arguments.get("payment_method_id")

        # Generate a payment ID from the intent
        payment_id = generate_id("pay")

        result = {
            "id": payment_id,
            "object": "payment_intent",
            "payment_intent_id": payment_intent_id,
            "amount": 50000,  # Mock amount
            "currency": "usd",
            "status": "succeeded",
            "payment_method": payment_method_id or generate_id("pm"),
            "created": int(datetime.now().timestamp()),
            "receipt_url": f"https://pay.stripe.com/receipts/{generate_id('', 32)}",
            "charges": {
                "data": [
                    {
                        "id": generate_id("ch"),
                        "amount": 50000,
                        "status": "succeeded",
                        "receipt_email": "customer@example.com"
                    }
                ]
            }
        }

        return MCPToolCallResult.json_result(result)

    @staticmethod
    async def refund_payment(arguments: dict) -> MCPToolCallResult:
        """Process a refund"""
        payment_id = arguments.get("payment_id", "")
        amount = arguments.get("amount")
        reason = arguments.get("reason", "requested_by_customer")

        refund_id = generate_id("re")

        result = {
            "id": refund_id,
            "object": "refund",
            "amount": amount or 50000,  # Full refund if no amount
            "charge": payment_id,
            "currency": "usd",
            "status": "succeeded",
            "reason": reason,
            "created": int(datetime.now().timestamp()),
        }

        return MCPToolCallResult.json_result(result)

    @staticmethod
    async def get_payment_status(arguments: dict) -> MCPToolCallResult:
        """Get payment status"""
        payment_id = arguments.get("payment_id", "")

        # Determine object type from prefix
        if payment_id.startswith("pi_"):
            obj_type = "payment_intent"
        else:
            obj_type = "charge"

        result = {
            "id": payment_id,
            "object": obj_type,
            "amount": 50000,
            "currency": "usd",
            "status": "succeeded",
            "created": int((datetime.now() - timedelta(hours=2)).timestamp()),
            "receipt_url": f"https://pay.stripe.com/receipts/{generate_id('', 32)}",
        }

        return MCPToolCallResult.json_result(result)

    @staticmethod
    async def create_customer(arguments: dict) -> MCPToolCallResult:
        """Create a customer"""
        email = arguments.get("email", "")
        name = arguments.get("name", "")
        metadata = arguments.get("metadata", {})

        customer_id = generate_id("cus")

        result = {
            "id": customer_id,
            "object": "customer",
            "email": email,
            "name": name,
            "metadata": metadata,
            "created": int(datetime.now().timestamp()),
            "default_source": None,
            "invoice_settings": {
                "default_payment_method": None
            }
        }

        return MCPToolCallResult.json_result(result)

    @staticmethod
    async def list_transactions(arguments: dict) -> MCPToolCallResult:
        """List transactions"""
        customer_id = arguments.get("customer_id")
        limit = arguments.get("limit", 10)

        # Generate mock transactions
        transactions = []
        for i in range(min(limit, 5)):  # Cap at 5 for mock data
            tx_time = datetime.now() - timedelta(days=i, hours=random.randint(0, 12))
            transactions.append({
                "id": generate_id("pay"),
                "object": "charge",
                "amount": random.randint(1000, 100000),
                "currency": "usd",
                "status": "succeeded",
                "customer": customer_id or generate_id("cus"),
                "created": int(tx_time.timestamp()),
                "description": f"Payment #{random.randint(1000, 9999)}",
            })

        result = {
            "object": "list",
            "data": transactions,
            "has_more": False,
            "total_count": len(transactions),
        }

        return MCPToolCallResult.json_result(result)

    @staticmethod
    async def get_balance(arguments: dict) -> MCPToolCallResult:
        """Get merchant balance"""
        result = {
            "object": "balance",
            "available": [
                {
                    "amount": 1250000,  # $12,500.00
                    "currency": "usd",
                    "source_types": {
                        "card": 1250000
                    }
                }
            ],
            "pending": [
                {
                    "amount": 75000,  # $750.00
                    "currency": "usd",
                    "source_types": {
                        "card": 75000
                    }
                }
            ],
            "livemode": False,
        }

        return MCPToolCallResult.json_result(result)

    @staticmethod
    async def create_payout(arguments: dict) -> MCPToolCallResult:
        """Create a payout to external account"""
        amount = arguments.get("amount", 0)
        destination = arguments.get("destination_account", "")
        description = arguments.get("description", "")

        payout_id = generate_id("po")

        result = {
            "id": payout_id,
            "object": "payout",
            "amount": amount,
            "currency": "usd",
            "status": "pending",
            "destination": destination,
            "description": description,
            "arrival_date": int((datetime.now() + timedelta(days=2)).timestamp()),
            "created": int(datetime.now().timestamp()),
            "method": "standard",
        }

        return MCPToolCallResult.json_result(result)

    @staticmethod
    async def send_webhook(arguments: dict) -> MCPToolCallResult:
        """Send webhook notification (mock - just returns success)"""
        url = arguments.get("url", "")
        event_type = arguments.get("event_type", "")
        payload = arguments.get("payload", {})

        webhook_id = generate_id("evt")

        result = {
            "id": webhook_id,
            "object": "event",
            "type": event_type,
            "created": int(datetime.now().timestamp()),
            "data": {
                "object": payload
            },
            "request": {
                "id": generate_id("req"),
                "idempotency_key": None
            },
            "webhook_status": {
                "url": url,
                "status": "sent",
                "response_code": 200
            }
        }

        return MCPToolCallResult.json_result(result)


# Map tool names to handlers
TOOL_HANDLERS = {
    "create_payment_intent": PaymentHandlers.create_payment_intent,
    "confirm_payment": PaymentHandlers.confirm_payment,
    "refund_payment": PaymentHandlers.refund_payment,
    "get_payment_status": PaymentHandlers.get_payment_status,
    "create_customer": PaymentHandlers.create_customer,
    "list_transactions": PaymentHandlers.list_transactions,
    "get_balance": PaymentHandlers.get_balance,
    "create_payout": PaymentHandlers.create_payout,
    "send_webhook": PaymentHandlers.send_webhook,
}
