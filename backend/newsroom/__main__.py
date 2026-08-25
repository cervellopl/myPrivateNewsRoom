"""Entry point: python -m newsroom [--host H] [--port P]"""
import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="newsroom", description="myPrivateNewsRoom server")
    parser.add_argument("--host", default=os.environ.get("NEWSROOM_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("NEWSROOM_PORT", 8000)))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("newsroom.api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
