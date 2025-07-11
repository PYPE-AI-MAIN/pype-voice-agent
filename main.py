import logging
import asyncio
import atexit
import os
import importlib.util
import importlib
import sys
from livekit.agents import cli, WorkerOptions
from dotenv import load_dotenv

config_path = os.environ.get("AGENT_CONFIG_PATH")
if not config_path:
    raise RuntimeError("AGENT_CONFIG_PATH environment variable not set")
if not os.path.isabs(config_path):
    config_path = os.path.join(os.path.dirname(__file__), config_path)
spec = importlib.util.spec_from_file_location("agent_runtime_config", config_path)
config = importlib.util.module_from_spec(spec)
sys.modules["agent_runtime_config"] = config
spec.loader.exec_module(config)

load_dotenv()

logger = logging.getLogger(config.AGENT_NAME)
logger.setLevel(logging.INFO)

@atexit.register
def shutdown_loop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.close()
    except:
        pass

entrypoint_mod = importlib.import_module("entrypoint")
entrypoint_fnc = getattr(entrypoint_mod, "entrypoint")

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint_fnc,
            agent_name=config.AGENT_NAME
        )
    )
