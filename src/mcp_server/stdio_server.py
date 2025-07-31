#!/usr/bin/env python3
"""STDIO MCP Server for Claude Desktop integration.

This server runs over STDIO (stdin/stdout) for Claude Desktop compatibility.
"""

import sys
import os
import builtins

def main():
    """Run the MCP server over STDIO."""
    try:
        # Save original print function and stdout
        original_print = builtins.print
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        
        # Replace print function with a no-op to suppress all print statements
        def silent_print(*args, **kwargs):
            pass
        
        builtins.print = silent_print
        
        # Redirect stderr to null
        sys.stderr = open(os.devnull, 'w')
        
        # Disable all logging
        import logging
        logging.disable(logging.CRITICAL)
        
        # Set environment variables
        os.environ["FASTMCP_NO_BANNER"] = "1"
        os.environ["PYTHONUNBUFFERED"] = "1"
        
        # Import and initialize server with print suppressed
        from mcp_server.mcp import ObservabilityMCPServer
        server = ObservabilityMCPServer()
        
        # Restore original stdout for MCP JSON-RPC communication
        sys.stdout = original_stdout
        
        # Keep print suppressed during tool execution
        # FastMCP will use sys.stdout.write() directly, bypassing print()
        
        # Run MCP server
        server.mcp.run(transport="stdio", show_banner=False)
        
    except Exception as e:
        # Debug: write error to stderr if possible
        try:
            with open('/tmp/mcp_error.log', 'w') as f:
                f.write(f"Error: {e}\n")
        except:
            pass
        sys.exit(1)

if __name__ == "__main__":
    main()
