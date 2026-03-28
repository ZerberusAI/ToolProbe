"""
Payment MCP Server

A mock Stripe-like payment processing MCP server for testing
server-side attacks (S1, S3) in the tool-calling evaluation framework.
"""

from .server import PaymentMCPServer

__all__ = ["PaymentMCPServer"]
