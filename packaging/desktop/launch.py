"""The frozen desktop app's entry (`packaging/desktop/worst-route-desktop.spec`): guarded, since a
spawned job worker runs this file again, and `main` turns it into the worker first thing."""

import sys

from worst_route.desktop import main

if __name__ == "__main__":
    sys.exit(main())
