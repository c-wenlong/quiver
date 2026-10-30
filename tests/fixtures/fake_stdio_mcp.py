"""A stdio MCP server for the gateway tests.

Like an older Python SDK server, it exits on a method it doesn't know.
Tool calls: ``echo`` answers with its pid and arguments, ``crash`` exits,
``ask`` sends the client a ``roots/list`` request and answers with what
came back, ``slow`` waits for its ``seconds`` argument first.
"""

import json
import os
import sys
import time

KNOWN = {"initialize", "tools/list", "tools/call", "ping"}


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if "id" not in message or method is None:
        continue  # notifications, and answers to our own requests
    if method not in KNOWN:
        sys.exit(f"unknown method {method}")
    if method == "initialize":
        result = {"protocolVersion": message["params"]["protocolVersion"],
                  "capabilities": {"tools": {"listChanged": True},
                                   "resources": {"subscribe": True, "listChanged": True}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/call":
        name = message["params"]["name"]
        args = message["params"].get("arguments") or {}
        if name == "crash":
            sys.exit(3)
        if name == "slow":
            time.sleep(float(args.get("seconds", 1)))
        if name == "ask":
            send({"jsonrpc": "2.0", "id": "srv-1", "method": "roots/list"})
            answer = json.loads(sys.stdin.readline())
            args = {"answer": answer}
        result = {"content": [{"type": "text", "text": json.dumps(
            {"pid": os.getpid(), "args": args})}]}
    else:
        result = {}
    send({"jsonrpc": "2.0", "id": message["id"], "result": result})
