"""Entry point for running the KNX Agent application.

Usage:
    python -m src                    # Run the brain
    python -m src setup-topology     # Generate house_topology.yaml from X1 API
"""

from __future__ import annotations

import sys


def _main() -> None:
    # Check for subcommands before importing heavy modules
    if len(sys.argv) > 1 and sys.argv[1] == "setup-topology":
        from src.cli.setup_topology import main as setup_main

        setup_main(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "health-snapshot":
        from src.cli.health_snapshot import main as health_snapshot_main

        health_snapshot_main(sys.argv[2:])
        return

    import asyncio

    from src.main import main

    asyncio.run(main())


if __name__ == "__main__":
    _main()
