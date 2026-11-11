"""Standalone entry point.

    python -m digital_msl serve [--port 8090]
    python -m digital_msl ask --repository <repository_id> "question" [--channel web|email|whatsapp]
    python -m digital_msl validate pre_validation.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)  # .env and relative SQLite paths resolve from backend/


def create_app():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    from digital_msl import store
    from digital_msl.router import router

    store.init_store()
    app = FastAPI(title="Digital MSL Engine", version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "healthy", "component": "digital_msl"}

    return app


async def _ask(args) -> int:
    from digital_msl import store
    from digital_msl.agents.query_agent import QueryAgent
    from digital_msl.contracts import InboundMessage

    message = InboundMessage(
        channel=args.channel,
        repository_id=args.repository,
        message_text=args.question,
        external_sender_id=args.sender,
        user_id=args.sender,
    )

    async def on_status(stage: str, data: dict) -> None:
        print(f"... {stage}: {data.get('detail', '')}", file=sys.stderr)

    from digital_msl.ikm_client import IkmClient

    response = await QueryAgent(retrieve_client=IkmClient()).handle(message, on_status=on_status)
    trace = store.get_trace(response.trace_id) or {}
    print("\n=== RETRIEVAL SNAPSHOT (before LLM claims) ===")
    print(json.dumps(trace.get("retrieval") or (trace.get("pre_validation") or {}).get("retrieval"), indent=2, ensure_ascii=False))
    print("\n=== PRE-VALIDATION JSON ===")
    print(json.dumps(trace.get("pre_validation"), indent=2, ensure_ascii=False))
    print("\n=== POST-VALIDATION JSON ===")
    print(json.dumps(trace.get("post_validation"), indent=2, ensure_ascii=False))
    print("\n=== RESPONSE ROUTED TO CHANNEL ===")
    print(json.dumps(response.model_dump(exclude={"claims"}), indent=2, ensure_ascii=False))
    return 0


async def _validate(args) -> int:
    from digital_msl.agents.validation_agent import ValidationAgent
    from digital_msl.contracts import ValidationPayload
    from digital_msl.llm import default_llm

    pre = ValidationPayload.model_validate_json(Path(args.file).read_text(encoding="utf-8"))
    post = await ValidationAgent(default_llm()).validate(pre)
    print(post.model_dump_json(indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="digital_msl")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the Digital MSL API on its own")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8090)

    ask = sub.add_parser("ask", help="answer one question and print both validation JSONs")
    ask.add_argument("question")
    ask.add_argument("--repository", required=True)
    ask.add_argument("--channel", default="web", choices=["web", "email", "whatsapp"])
    ask.add_argument("--sender", default="cli_user")

    val = sub.add_parser("validate", help="run only the validation engine on a pre-validation JSON file")
    val.add_argument("file")

    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.command == "serve":
        import uvicorn

        logging.getLogger().setLevel(logging.INFO)
        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0
    if args.command == "ask":
        return asyncio.run(_ask(args))
    return asyncio.run(_validate(args))


if __name__ == "__main__":
    raise SystemExit(main())
