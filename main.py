import logging
import asyncio
import atexit
import os
from livekit.agents import cli, WorkerOptions
from entrypoint import entrypoint
from dotenv import load_dotenv
from agent.agent_runtime_config import AGENT_NAME



load_dotenv()

logger = logging.getLogger(AGENT_NAME)
logger.setLevel(logging.INFO)

@atexit.register
def shutdown_loop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.close()
    except:
        pass

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=AGENT_NAME
        )
    )
