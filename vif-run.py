"""Minimal VIF STT server start."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from shared.version_check import check_python_version

check_python_version()

os.environ["VIF_LOG_LEVEL"] = os.environ.get("VIF_LOG_LEVEL", "INFO")

from services.stt_server import main

if __name__ == "__main__":
    main()
