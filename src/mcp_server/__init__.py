"""mcp_server package init.

Ensures the monorepo `src/` directory is on sys.path so sibling modules like
`core` can be imported when running the installed console script.
"""

from pathlib import Path
import sys

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


