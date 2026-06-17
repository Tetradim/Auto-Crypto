from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("AUTO_CRYPTO_HOST", "127.0.0.1")
    port = int(os.getenv("AUTO_CRYPTO_PORT", "8004"))
    uvicorn.run("autocrypto.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
