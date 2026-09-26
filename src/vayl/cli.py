"""Console entry points. Arguments are parsed here, before the server modules are imported:
importing vayl.api.mcp_server opens the database, so `vayl-mcp --help` must answer first or it
would create vayl.db in the current directory. All configuration is environment variables."""
import argparse

from vayl import __version__

_DOCS = "Configuration: environment variables, see https://vayl.gitbook.io/vayl-docs"


def _parse(prog, description, argv):
    parser = argparse.ArgumentParser(prog=prog, description=description, epilog=_DOCS)
    parser.add_argument("--version", action="version", version=f"{prog} {__version__}")
    parser.parse_args(argv)


def mcp(argv=None):
    _parse("vayl-mcp", "Vayl MCP server over stdio, for one local user. "
           "Run it from an MCP client (Claude Desktop, Cursor, Claude Code), not by hand.", argv)
    from vayl.api import mcp_server
    mcp_server.main()


def server(argv=None):
    _parse("vayl-server", "Vayl MCP server over authenticated streamable HTTP, for a team. "
           "Listens on VAYL_HOST:VAYL_PORT (default 127.0.0.1:8080).", argv)
    from vayl.api import server as http_server
    http_server.main()
