import sys
from pathlib import Path

# Позволяет запускать pytest без editable-install (src-layout).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
