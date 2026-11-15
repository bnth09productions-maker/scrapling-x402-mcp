"""python -m scrapling_mcp"""

import os

from scrapling_mcp.server import serve


def main() -> None:
    host = os.environ.get("SCRAPLING_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("SCRAPLING_MCP_PORT", "8765"))
    serve(host, port)


if __name__ == "__main__":
    main()
