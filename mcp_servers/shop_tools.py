"""Real MCP stdio server over the same tool registry (JD bonus: MCP server development).
Run: PYTHONPATH=. python -m mcp_servers.shop_tools   (stdio transport)
The agent runtime uses LocalGateway in-process by default; McpGateway can swap to this server."""
from mcp.server.fastmcp import FastMCP

from packages.core import tools

mcp = FastMCP("afterpilot-shop")


@mcp.tool()
def order_query(order_id: str = "", customer_id: str = "") -> dict:
    """Query one order by id, or list orders of a customer."""
    return tools.fn_order_query({"order_id": order_id, "customer_id": customer_id})


@mcp.tool()
def logistics_track(order_id: str) -> dict:
    """Track logistics status / stagnation for an order."""
    return tools.fn_logistics_track({"order_id": order_id})


@mcp.tool()
def refund_query(order_id: str = "", key: str = "") -> dict:
    """Query refunds by order or by idempotency key (reconciliation)."""
    return tools.fn_refund_query({"order_id": order_id, "key": key})


@mcp.tool()
def refund_create(order_id: str, amount: float, reason: str, idempotency_key: str) -> dict:
    """Execute a refund. Idempotent via idempotency_key; policy engine enforces hard limits upstream."""
    return tools.fn_refund_create({"order_id": order_id, "amount": amount, "reason": reason}, idempotency_key)


@mcp.tool()
def invoice_issue(order_id: str, title: str, tax_no: str, idempotency_key: str) -> dict:
    """Issue an e-invoice for a delivered order. Idempotent via idempotency_key."""
    return tools.fn_invoice_issue({"order_id": order_id, "title": title, "tax_no": tax_no}, idempotency_key)


@mcp.tool()
def kb_search(query: str, k: int = 3) -> dict:
    """Search the versioned policy knowledge base."""
    return tools.fn_kb_search({"query": query, "k": k})


if __name__ == "__main__":
    mcp.run()