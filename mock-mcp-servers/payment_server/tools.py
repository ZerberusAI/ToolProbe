"""
Payment Tool Definitions

Defines the 8 payment tools provided by the Payment MCP Server,
modeled after Stripe's API.
"""

import sys
from pathlib import Path

# Add base module to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from base.protocol import MCPTool, MCPToolParameter


def get_payment_tools() -> list[MCPTool]:
    """Return all payment tools provided by this server"""
    return [
        MCPTool(
            name="create_payment_intent",
            description="Create a new payment intent for processing a payment. "
                        "Returns a payment intent ID that can be used to confirm the payment.",
            parameters=[
                MCPToolParameter(
                    name="amount",
                    type="integer",
                    description="Amount to charge in cents (e.g., 5000 for $50.00)",
                    required=True
                ),
                MCPToolParameter(
                    name="currency",
                    type="string",
                    description="Three-letter ISO currency code (e.g., 'usd', 'eur')",
                    required=True,
                    enum=["usd", "eur", "gbp", "cad", "aud"]
                ),
                MCPToolParameter(
                    name="customer_id",
                    type="string",
                    description="ID of the customer making the payment",
                    required=False
                ),
                MCPToolParameter(
                    name="description",
                    type="string",
                    description="Description of the payment for records",
                    required=False
                ),
            ]
        ),
        MCPTool(
            name="confirm_payment",
            description="Confirm and process a pending payment intent. "
                        "This will charge the customer's payment method.",
            parameters=[
                MCPToolParameter(
                    name="payment_intent_id",
                    type="string",
                    description="The payment intent ID to confirm (starts with 'pi_')",
                    required=True
                ),
                MCPToolParameter(
                    name="payment_method_id",
                    type="string",
                    description="The payment method to charge (starts with 'pm_')",
                    required=False
                ),
            ]
        ),
        MCPTool(
            name="refund_payment",
            description="Process a full or partial refund for a completed payment.",
            parameters=[
                MCPToolParameter(
                    name="payment_id",
                    type="string",
                    description="The payment ID to refund (starts with 'pay_')",
                    required=True
                ),
                MCPToolParameter(
                    name="amount",
                    type="integer",
                    description="Amount to refund in cents. If not specified, full refund.",
                    required=False
                ),
                MCPToolParameter(
                    name="reason",
                    type="string",
                    description="Reason for the refund",
                    required=False,
                    enum=["duplicate", "fraudulent", "requested_by_customer", "other"]
                ),
            ]
        ),
        MCPTool(
            name="get_payment_status",
            description="Get the current status and details of a payment.",
            parameters=[
                MCPToolParameter(
                    name="payment_id",
                    type="string",
                    description="The payment ID to check (starts with 'pay_' or 'pi_')",
                    required=True
                ),
            ]
        ),
        MCPTool(
            name="create_customer",
            description="Create a new customer profile for storing payment methods.",
            parameters=[
                MCPToolParameter(
                    name="email",
                    type="string",
                    description="Customer's email address",
                    required=True
                ),
                MCPToolParameter(
                    name="name",
                    type="string",
                    description="Customer's full name",
                    required=False
                ),
                MCPToolParameter(
                    name="metadata",
                    type="object",
                    description="Additional metadata to store with the customer",
                    required=False
                ),
            ]
        ),
        MCPTool(
            name="list_transactions",
            description="List transactions with optional filters.",
            parameters=[
                MCPToolParameter(
                    name="customer_id",
                    type="string",
                    description="Filter by customer ID",
                    required=False
                ),
                MCPToolParameter(
                    name="start_date",
                    type="string",
                    description="Start date for transaction range (ISO 8601 format)",
                    required=False
                ),
                MCPToolParameter(
                    name="end_date",
                    type="string",
                    description="End date for transaction range (ISO 8601 format)",
                    required=False
                ),
                MCPToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum number of transactions to return (default: 10)",
                    required=False,
                    default=10
                ),
            ]
        ),
        MCPTool(
            name="get_balance",
            description="Get the current balance of the merchant account.",
            parameters=[]
        ),
        MCPTool(
            name="create_payout",
            description="Transfer funds from merchant account to an external bank account.",
            parameters=[
                MCPToolParameter(
                    name="amount",
                    type="integer",
                    description="Amount to transfer in cents",
                    required=True
                ),
                MCPToolParameter(
                    name="destination_account",
                    type="string",
                    description="Bank account ID or external account to transfer to",
                    required=True
                ),
                MCPToolParameter(
                    name="description",
                    type="string",
                    description="Description for the payout",
                    required=False
                ),
            ]
        ),
        MCPTool(
            name="send_webhook",
            description="Send a webhook notification to a specified URL with payment event data.",
            parameters=[
                MCPToolParameter(
                    name="url",
                    type="string",
                    description="The webhook URL to send the notification to",
                    required=True
                ),
                MCPToolParameter(
                    name="event_type",
                    type="string",
                    description="Type of event to send",
                    required=True,
                    enum=[
                        "payment.completed",
                        "payment.failed",
                        "refund.created",
                        "customer.created"
                    ]
                ),
                MCPToolParameter(
                    name="payload",
                    type="object",
                    description="Event payload data to include",
                    required=True
                ),
            ]
        ),
    ]
