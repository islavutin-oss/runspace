"""runspace.workspace.serve — zero-code launcher."""

from __future__ import annotations

import os
import sys

from .backend.bootstrap import create_app


def main(argv: list[str] | None = None) -> None:
    argv = list(argv if argv is not None else sys.argv[1:])

    if argv and not argv[0].startswith("-"):
        workspace_yml = argv[0]
    else:
        workspace_yml = os.environ.get("WORKSPACE_YML", "")

    if not workspace_yml:
        print(
            "ERROR: workspace.yml path required. Pass as argv[1] or set WORKSPACE_YML env var.",
            file=sys.stderr,
        )
        sys.exit(2)

    app = create_app(
        workspace_yml=workspace_yml,
        tenant_id=os.environ.get("TENANT_ID") or None,
    )

    try:
        import uvicorn
    except ImportError:
        print(
            "ERROR: uvicorn not installed. Add it to the tenant's "
            "requirements (or `pip install uvicorn`).",
            file=sys.stderr,
        )
        sys.exit(3)

    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
