"""The voice worker process: ``python -m backend.connectors.voice.worker`` (started by ``host``).

It reads its settings from the app on stdin, connects OUT to the owner's LiveKit project and
takes calls, asking the app (``app_link``) for everything that is the app's. stdout is the
link: anything else that writes there is sent to stderr, which is the app's log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys


def main() -> int:
    link_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s:     voice-worker %(name)s: %(message)s",
    )
    rx = sys.stdin.buffer
    try:
        init = json.loads(rx.readline() or b"{}")
    except ValueError:
        init = {}
    if init.get("op") != "init" or not isinstance(init.get("config"), dict):
        logging.error("voice-worker: started without settings from the app")
        return 2

    # LiveKit's plugins register only on the main thread: import them here, first.
    from backend.connectors.voice import agent
    from backend.connectors.voice.app_link import AppLink

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    server = None

    def on_stop() -> None:
        if server is not None:
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(server.aclose()))
        else:
            os._exit(0)

    link = AppLink(rx, link_out, on_stop=on_stop)
    config = init["config"]
    agent.RUNTIME = agent.Runtime(
        app=link,
        config=config,
        owner_name=str(init.get("owner") or ""),
        language_name=str(init.get("language_name") or "English"),
    )
    server = _build_server(config)
    link.send({"op": "ready"})
    loop.run_until_complete(server.run())
    return 0


def _build_server(config: dict):
    from livekit.agents import AgentServer
    from livekit.agents.job import JobExecutorType

    from backend.connectors.voice import agent
    from backend.connectors.voice.tokens import AGENT_NAME

    server = AgentServer(
        ws_url=config["livekit_url"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        job_executor_type=JobExecutorType.THREAD,
        num_idle_processes=1,
        initialize_process_timeout=60.0,
        # One owner never needs load shedding; a busy laptop tripped the 0.7 default.
        load_threshold=1.0,
        # Its health-check server took a fixed :8081, still held by the previous process for a
        # moment on restart, so the worker failed to start (live, 2026-11-03). We never read it:
        # any free loopback port.
        host="127.0.0.1",
        port=0,
        setup_fnc=agent.prewarm,
    )
    server.rtc_session(agent.entrypoint, agent_name=AGENT_NAME, on_request=agent.on_request)
    return server


if __name__ == "__main__":
    raise SystemExit(main())
