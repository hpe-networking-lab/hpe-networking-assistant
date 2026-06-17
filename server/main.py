"""Bootstrap entry point for the packaged Claude Desktop extension.

The build process copies the ``hpe_mist_mcp`` package next to this file and
installs runtime dependencies into ``server/lib``. This shim ensures both are
importable before starting the MCP server, regardless of the user's global
Python environment.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

# Bundled third-party dependencies (e.g. the `mcp` package).
_LIB = os.path.join(_HERE, "lib")
if os.path.isdir(_LIB):
    sys.path.insert(0, _LIB)

# The hpe_mist_mcp package shipped alongside this shim.
sys.path.insert(0, _HERE)

from hpe_mist_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
