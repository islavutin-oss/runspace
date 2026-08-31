"""Entry point for `python -m runspace.workspace.cli`.

Most users prefer the `runspace` console script (installed by the
runspace pyproject); both end up calling main() below.
"""

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runspace",
        description="Scaffold + run tenants on the runspace platform.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="scaffold a new tenant")
    init.add_argument("tenant_id", help="tenant slug (e.g. 'lupita-tacos')")
    init.add_argument(
        "--target-dir",
        "-t",
        default=".",
        help="directory under which tenants/<id>/ is created (default: cwd)",
    )
    init.add_argument(
        "--name",
        help="display name (default: prompted)",
    )
    init.add_argument(
        "--icon",
        default="🗂",
        help="emoji icon for the workspace (default: 🗂)",
    )
    init.add_argument(
        "--brand-color",
        default="#3B82F6",
        help="hex brand color (default: #3B82F6)",
    )
    init.add_argument(
        "--no-interactive",
        action="store_true",
        help="skip prompts; use defaults / flags only",
    )

    serve = sub.add_parser("serve", help="run the workspace.serve launcher")
    serve.add_argument(
        "workspace_yml",
        nargs="?",
        default=None,
        help="path to workspace.yml (or set $WORKSPACE_YML)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "init":
        from .init_cmd import run_init

        return run_init(
            tenant_id=args.tenant_id,
            target_dir=Path(args.target_dir).resolve(),
            name=args.name,
            icon=args.icon,
            brand_color=args.brand_color,
            interactive=not args.no_interactive,
        )

    if args.cmd == "serve":
        from runspace.workspace.serve import main as serve_main

        argv2 = [args.workspace_yml] if args.workspace_yml else []
        serve_main(argv2)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
