#!/usr/bin/env python3
"""
mcp_server.py — Model Context Protocol (MCP) stdio server for 'llms-txt-agent-packager'.
Allows Claude Desktop, Cursor, Devin, and autonomous coding agents to call this tool directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Import local audit site logic
from main import audit_site, audit_many

SERVER_NAME = "llms-txt-agent-packager-mcp"
SERVER_VERSION = "0.1.0"


def send_response(response: dict):
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


def handle_request(req: dict):
    method = req.get("method")
    msg_id = req.get("id")

    if method == "initialize":
        send_response({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION
                }
            }
        })
    elif method == "notifications/initialized":
        pass  # Notification, no response
    elif method == "tools/list":
        send_response({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": "audit_llms_txt",
                        "description": "Audits a website for AI agent readability (GPTBot, ClaudeBot, PerplexityBot in robots.txt), checks for existing /llms.txt, and generates a compliant llms.txt with token estimation.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "domain": {
                                    "type": "string",
                                    "description": "Website or documentation domain to audit (e.g. stripe.com, docs.anthropic.com)"
                                },
                                "max_pages": {
                                    "type": "integer",
                                    "description": "Maximum pages to crawl for documentation links (default: 4)",
                                    "default": 4
                                }
                            },
                            "required": ["domain"]
                        }
                    }
                ]
            }
        })
    elif method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name == "audit_llms_txt":
            domain = args.get("domain", "stripe.com")
            max_pages = int(args.get("max_pages", 4))
            try:
                res = audit_site(domain, max_pages)
                send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(res, indent=2)
                            }
                        ]
                    }
                })
            except Exception as e:
                send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error auditing {domain}: {type(e).__name__} - {e}"
                            }
                        ]
                    }
                })
        else:
            send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {tool_name}"
                }
            })
    else:
        if msg_id is not None:
            send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Unhandled method: {method}"
                }
            })


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            handle_request(req)
        except Exception as e:
            sys.stderr.write(f"Error handling MCP line: {e}\n")


if __name__ == "__main__":
    main()
