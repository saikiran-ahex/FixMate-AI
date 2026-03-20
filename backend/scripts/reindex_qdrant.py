from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import QdrantKnowledgeBase


async def main() -> None:
    knowledge_base = QdrantKnowledgeBase()
    result = await knowledge_base.reindex()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
