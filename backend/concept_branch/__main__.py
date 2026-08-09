import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "concept_branch.app:app",
        host=os.environ.get("CONCEPT_BRANCH_HOST", "127.0.0.1"),
        port=int(os.environ.get("CONCEPT_BRANCH_PORT", "8421")),
        reload=False,
    )
