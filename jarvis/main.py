"""Jarvis CLI and Core Server entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import sys

from jarvis.config import Settings
from jarvis.orchestrator import JarvisOrchestrator


async def _run_turn(orchestrator: JarvisOrchestrator, prompt: str) -> int:
    saw_delta = False
    had_error = False
    async for event in orchestrator.chat_stream(prompt, channel="cli"):
        if event.kind == "delta":
            saw_delta = True
            sys.stdout.write(event.text)
            sys.stdout.flush()
        elif event.kind == "tool" and event.tool_name:
            sys.stderr.write(f"\n[jarvis: running {event.tool_name}...]\n")
            sys.stderr.flush()
        elif event.kind == "result":
            if not saw_delta and event.text:
                sys.stdout.write(event.text)
                sys.stdout.flush()
        elif event.kind == "error":
            had_error = True
            sys.stderr.write(f"\n[jarvis error] {event.text}\n")
            sys.stderr.flush()

    if saw_delta:
        sys.stdout.write("\n")
        sys.stdout.flush()
    return 1 if had_error else 0


async def _interactive_loop(orchestrator: JarvisOrchestrator) -> int:
    print(f"Jarvis ready (model: {orchestrator.brain.model}). Type /help for commands, Ctrl+D to exit.\n")
    while True:
        try:
            user_input = await asyncio.to_thread(input, "you> ")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return 0

        text = user_input.strip()
        if not text:
            continue
        if text.lower() in ("/exit", "/quit", "exit", "quit"):
            print("Goodbye.")
            return 0

        sys.stdout.write("jarvis> ")
        sys.stdout.flush()
        await _run_turn(orchestrator, text)
        print()


def _serve(
    settings: Settings,
    host: str,
    port: int,
    model: str | None,
    mode: str | None,
    project: str | None,
    enable_telegram: bool = False,
) -> int:
    try:
        import uvicorn
        from jarvis.server import create_app
    except ImportError as exc:
        sys.stderr.write(
            f"Web server dependencies not installed ({exc}).\n"
            "Run: uv pip install --python .venv/bin/python -e '.[web]'\n"
        )
        return 1

    app = create_app(settings, enable_telegram=enable_telegram)
    orch = app.state.orchestrator
    if mode:
        orch.brain.set_mode(mode)
    if project:
        orch.brain.set_workspace(project)
    if model:
        orch.brain.model = model
    tg_note = " + Telegram" if enable_telegram else ""
    print(
        f"Starting Jarvis Core Service + Web UI{tg_note} at http://{host}:{port} "
        f"(mode: {orch.brain.mode}, model: {orch.brain.model})"
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def _run_telegram(
    settings: Settings,
    model: str | None,
    mode: str | None,
    project: str | None,
) -> int:
    try:
        from jarvis.telegram_bot import run_telegram_bot
    except ImportError as exc:
        sys.stderr.write(
            f"Telegram dependencies not installed ({exc}).\n"
            "Run: uv pip install --python .venv/bin/python -e '.[telegram]'\n"
        )
        return 1

    orch = JarvisOrchestrator(settings)
    if mode:
        orch.brain.set_mode(mode)
    if project:
        orch.brain.set_workspace(project)
    if model:
        orch.brain.model = model

    try:
        run_telegram_bot(orch, settings=settings)
    except RuntimeError as exc:
        sys.stderr.write(f"Telegram bot error: {exc}\n")
        return 1
    return 0


def _run_voice(
    settings: Settings,
    model: str | None,
    mode: str | None,
    project: str | None,
    push_to_talk: bool,
    api_url: str | None,
) -> int:
    from jarvis.voice import VoiceConfig, VoiceDaemon

    cfg = VoiceConfig(api_url=api_url or "")
    orch = None
    if not cfg.api_url:
        orch = JarvisOrchestrator(settings)
        if mode:
            orch.brain.set_mode(mode)
        if project:
            orch.brain.set_workspace(project)
        if model:
            orch.brain.model = model

    daemon = VoiceDaemon(orchestrator=orch, config=cfg, settings=settings)
    return asyncio.run(daemon.run(push_to_talk=push_to_talk))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="Jarvis personal AI assistant")
    parser.add_argument("-p", "--prompt", help="Run a single prompt non-interactively and exit")
    parser.add_argument("--mode", choices=["assistant", "code"], help="Start in assistant or code mode")
    parser.add_argument("--project", help="Set active workspace directory for code mode")
    parser.add_argument("--model", help="Override the Antigravity model (e.g. gemini-4-neon-medium)")
    parser.add_argument("--reset", action="store_true", help="Reset conversation state before running")
    parser.add_argument("--serve", action="store_true", help="Start the FastAPI Core Service + Local Web UI")
    parser.add_argument("--telegram", action="store_true", help="Start the free Telegram bot bridge")
    parser.add_argument("--voice", action="store_true", help="Start the local hands-free Voice Daemon")
    parser.add_argument("--push-to-talk", action="store_true", help="Use Enter-to-speak instead of wake word with --voice")
    parser.add_argument("--api-url", help="Connect --voice to a running Core Service (e.g. http://127.0.0.1:8765)")
    parser.add_argument("--host", help="Host to bind when using --serve (default from JARVIS_HOST)")
    parser.add_argument("--port", type=int, help="Port to bind when using --serve (default from JARVIS_PORT)")
    args = parser.parse_args(argv)

    settings = Settings.load()

    if args.serve:
        return _serve(
            settings,
            host=args.host or settings.host,
            port=args.port or settings.port,
            model=args.model,
            mode=args.mode,
            project=args.project,
            enable_telegram=args.telegram,
        )

    if args.telegram:
        return _run_telegram(
            settings,
            model=args.model,
            mode=args.mode,
            project=args.project,
        )

    if args.voice:
        return _run_voice(
            settings,
            model=args.model,
            mode=args.mode,
            project=args.project,
            push_to_talk=args.push_to_talk,
            api_url=args.api_url,
        )

    orchestrator = JarvisOrchestrator(settings)
    if args.mode:
        orchestrator.brain.set_mode(args.mode)
    if args.project:
        orchestrator.brain.set_workspace(args.project)
    if args.model:
        orchestrator.brain.model = args.model
    if args.reset:
        orchestrator.brain.reset_conversation()

    if args.prompt:
        return asyncio.run(_run_turn(orchestrator, args.prompt))
    return asyncio.run(_interactive_loop(orchestrator))


if __name__ == "__main__":
    raise SystemExit(main())
