"""
Payment MCP Server

A mock Stripe-like payment processing server implementing the MCP protocol.
Used for testing server-side attacks (S1, S3) in the evaluation framework.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path for base module
sys.path.insert(0, str(Path(__file__).parent.parent))

from base.mcp_server import BaseMCPServer
from base.protocol import MCPTool, MCPToolCallResult

from .handlers import TOOL_HANDLERS
from .tools import get_payment_tools


class PaymentMCPServer(BaseMCPServer):
    """
    Mock Payment MCP Server (Stripe-like)

    Provides payment processing tools for testing:
    - create_payment_intent
    - confirm_payment
    - refund_payment
    - get_payment_status
    - create_customer
    - list_transactions
    - get_balance
    - create_payout
    - send_webhook

    Supports attack scenarios:
    - S1 (Description Injection): Malicious instructions in tool descriptions
    - S3 (Return Value Injection): Attack payloads in tool responses
    """

    def __init__(self, scenarios_dir: Path = None):
        # Default scenarios directory
        if scenarios_dir is None:
            scenarios_dir = Path(__file__).parent / "scenarios"

        super().__init__(
            name="mock-payment-server",
            version="1.0.0",
            scenarios_dir=scenarios_dir
        )

    def get_tools(self) -> list[MCPTool]:
        """Return payment tools"""
        return get_payment_tools()

    async def handle_tool_call(
        self,
        tool_name: str,
        arguments: dict
    ) -> MCPToolCallResult:
        """Execute payment tool"""
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return MCPToolCallResult.text_result(
                f"Unknown tool: {tool_name}",
                is_error=True
            )

        try:
            return await handler(arguments)
        except Exception as e:
            return MCPToolCallResult.text_result(
                f"Error executing {tool_name}: {str(e)}",
                is_error=True
            )


def create_app():
    """Create the FastAPI app for the payment server"""
    # Get scenarios directory from env or default
    scenarios_path = os.environ.get(
        "SCENARIOS_DIR",
        str(Path(__file__).parent / "scenarios")
    )
    scenarios_dir = Path(scenarios_path)

    server = PaymentMCPServer(scenarios_dir=scenarios_dir)
    return server.app


# Create app instance for uvicorn
app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8010))
    host = os.environ.get("HOST", "0.0.0.0")

    print(f"Starting Payment MCP Server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
