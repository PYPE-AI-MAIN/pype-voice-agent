import os
import shutil
import logging
from pydantic import BaseModel
from typing import List, Dict, Any
import yaml

# Initialize logger
logger = logging.getLogger("create-agent")
logging.basicConfig(level=logging.INFO)

ASSISTANT_TEMPLATE = '''from livekit.plugins import {backend_imports}
from base_agent import BaseAgent
from user_data import RunContext_T
from livekit.agents import (function_tool)
from livekit import api
from livekit.agents.job import get_job_context

class {assistant_class}(BaseAgent):
    def __init__(self):
        super().__init__(
            instructions={instructions!r},
            stt={stt_backend}.STT(language={stt_language!r}, model={stt_model!r}),
            llm={llm_backend}.LLM(model={llm_model!r}, temperature={llm_temperature}),
            tts={tts_backend}.TTS(
                voice_id={tts_voice_id!r},
                model={tts_model!r},
                language={tts_language!r},
                voice_settings={tts_backend}.VoiceSettings(**{tts_voice_settings})
            ),
            vad={vad_backend}.VAD.load(min_silence_duration={vad_min_silence_duration})
        )

    @function_tool()
    async def end_call(self, ctx: RunContext_T):
        """Use this tool to end the call immediately."""
        # Wait for current speech to finish
        current_speech = ctx.session.current_speech
        if current_speech:
            await current_speech.wait_for_playout()

        # Now hang up using the server API
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )
'''

AGENT_RUNTIME_CONFIG_TEMPLATE = '''{assistant_imports}
ASSISTANT_CLASSES = [{assistant_classes}]
ASSISTANT_NAMES = [{assistant_names}]
AGENT_NAME = "{agent_name}"
AGENT_TYPE= "INBOUND"
'''

# --- Pydantic Models for FastAPI ---
class STTConfig(BaseModel):
    name: str
    language: str
    model: str

class LLMConfig(BaseModel):
    name: str
    model: str
    temperature: float
    custom_option: Any = None

class TTSVoiceSettings(BaseModel):
    similarity_boost: float
    stability: float
    style: float
    use_speaker_boost: bool
    speed: float

class TTSConfig(BaseModel):
    name: str
    voice_id: str
    language: str
    model: str
    voice_settings: TTSVoiceSettings

class VADConfig(BaseModel):
    name: str
    min_silence_duration: float

class AssistantConfig(BaseModel):
    name: str
    prompt: str
    stt: STTConfig
    llm: LLMConfig
    tts: TTSConfig
    vad: VADConfig

class AgentConfig(BaseModel):
    name: str
    assistant: List[AssistantConfig]

class AgentRequest(BaseModel):
    agent: AgentConfig

def create_agent(agent_config: dict):
    agent = agent_config["agent"]
    agent_name = agent["name"]
    assistants = agent["assistant"]
    base_path = os.path.join(os.path.dirname(__file__), "..", "agent", agent_name)
    assistants_path = os.path.join(base_path, "assistants")
    logger.info(f"Creating inbound agent directory structure for '{agent_name}' at '{base_path}'")
    os.makedirs(assistants_path, exist_ok=True)

    # Collect all unique backends for import
    backend_set = set()
    for assistant in assistants:
        backend_set.add(assistant["stt"]["name"])
        backend_set.add(assistant["llm"]["name"])
        backend_set.add(assistant["tts"]["name"])
        backend_set.add(assistant["vad"]["name"])
    backend_imports = ", ".join(sorted(backend_set))

    # Write each assistant
    assistant_imports = []
    assistant_classes = []
    assistant_names = []
    for assistant in assistants:
        class_name = assistant["name"]
        file_name = f"{class_name}.py"
        assistant_path = os.path.join(assistants_path, file_name)
        logger.info(f"Writing assistant class '{class_name}' to {assistant_path}")
        assistant_imports.append(f"from agent.{agent_name}.assistants.{class_name} import {class_name}")
        assistant_classes.append(class_name)
        assistant_names.append(f'"{class_name}"')
        with open(assistant_path, "w") as f:
            f.write(ASSISTANT_TEMPLATE.format(
                backend_imports=backend_imports,
                assistant_class=class_name,
                instructions=assistant["prompt"],
                stt_backend=assistant["stt"]["name"],
                stt_language=assistant["stt"]["language"],
                stt_model=assistant["stt"]["model"],
                llm_backend=assistant["llm"]["name"],
                llm_model=assistant["llm"]["model"],
                llm_temperature=assistant["llm"].get("temperature", 0.3),
                tts_backend=assistant["tts"]["name"],
                tts_voice_id=assistant["tts"]["voice_id"],
                tts_model=assistant["tts"]["model"],
                tts_language=assistant["tts"]["language"],
                tts_voice_settings=assistant["tts"]["voice_settings"],
                vad_backend=assistant["vad"]["name"],
                vad_min_silence_duration=assistant["vad"].get("min_silence_duration", 0.2)
            ))

    # Write agent-level config.yaml
    config_yaml_path = os.path.join(base_path, "config.yaml")
    with open(config_yaml_path, "w") as f:
        yaml.safe_dump({"agent": agent}, f, sort_keys=False)

    # Write agent_runtime_config.py
    agent_runtime_config_path = os.path.join(base_path, "agent_runtime_config.py")
    logger.info(f"Writing agent runtime config to {agent_runtime_config_path}")
    with open(agent_runtime_config_path, "w") as f:
        f.write(AGENT_RUNTIME_CONFIG_TEMPLATE.format(
            assistant_imports="\n".join(assistant_imports),
            assistant_classes=", ".join(assistant_classes),
            assistant_names=", ".join(assistant_names),
            agent_name=agent_name
        ))
    logger.info(f"Inbound agent '{agent_name}' creation complete.")
