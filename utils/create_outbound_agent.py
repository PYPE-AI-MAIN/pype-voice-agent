import os
import shutil
import logging
from pydantic import BaseModel
from typing import List, Dict, Any

# Initialize logger
logger = logging.getLogger("create-outbound-agent")
logging.basicConfig(level=logging.INFO)

# --- TEMPLATES ---

BASE_AGENT_TEMPLATE = '''import logging
from livekit.agents import Agent
from typing import Optional
from agent.{agent_name}.user.user_data import UserData, RunContext_T

logger = logging.getLogger("agent")

class BaseAgent(Agent):
    async def on_enter(self) -> None:
        agent_name = self.__class__.__name__
        logger.info(f"Entering {{agent_name}}")
        userdata: UserData = self.session.userdata
        if userdata.ctx and userdata.ctx.room:
            await userdata.ctx.room.local_participant.set_attributes({{"agent": agent_name}})
        chat_ctx = self.chat_ctx.copy()
        if userdata.prev_agent:
            items_copy = self._truncate_chat_ctx(userdata.prev_agent.chat_ctx.items, keep_function_call=True)
            existing_ids = {{item.id for item in chat_ctx.items}}
            items_copy = [item for item in items_copy if item.id not in existing_ids]
            chat_ctx.items.extend(items_copy)
        chat_ctx.add_message(
            role="system",
            content=f"You are the {{agent_name}}. {{userdata.summarize()}}"
        )
        await self.update_chat_ctx(chat_ctx)
        self.session.generate_reply()

    def _truncate_chat_ctx(self, items: list, keep_last_n_messages: int = 6,
                           keep_system_message: bool = False, keep_function_call: bool = False) -> list:
        def _valid_item(item) -> bool:
            if not keep_system_message and item.type == "message" and item.role == "system":
                return False
            if not keep_function_call and item.type in ["function_call", "function_call_output"]:
                return False
            return True
        new_items = [item for item in reversed(items) if _valid_item(item)]
        new_items = new_items[:keep_last_n_messages][::-1]
        while new_items and new_items[0].type in ["function_call", "function_call_output"]:
            new_items.pop(0)
        return new_items

    async def _transfer_to_agent(self, name: str, context: RunContext_T) -> Agent:
        userdata = context.userdata
        userdata.prev_agent = context.session.current_agent
        return userdata.personas[name]
'''

USER_DATA_TEMPLATE = '''from dataclasses import dataclass, field
from typing import Optional
from livekit.agents import Agent, JobContext, RunContext

@dataclass
class UserData:
    personas: dict = field(default_factory=dict)
    prev_agent: Optional[Agent] = None
    ctx: Optional[JobContext] = None

    def summarize(self) -> str:
        return "User data: user voice agent"

RunContext_T = RunContext[UserData]
'''

ENTRYPOINT_TEMPLATE = '''import os
import json
from dotenv import load_dotenv
from livekit import api
from livekit.agents import AgentSession, JobContext, RoomInputOptions
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.plugins import noise_cancellation
from agent.{agent_name}.user.user_data import UserData
from agent.{agent_name}.agent_runtime_config import ASSISTANT_CLASSES as ASSISTANT_CLASSES_KT1

load_dotenv()

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
    try:
        dial_info = json.loads(ctx.job.metadata)
        phone_number = dial_info.get("phone_number", None)
    except Exception:
        phone_number = None
    await ctx.api.sip.create_sip_participant(
        api.CreateSIPParticipantRequest(
            room_name=ctx.room.name,
            sip_trunk_id=outbound_trunk_id,
            sip_call_to=phone_number,
            participant_identity=phone_number,
            wait_until_answered=True,
        )
    )
    userdata = UserData(ctx=ctx)
    agent_instances = []
    for cls in ASSISTANT_CLASSES_KT1:
        instance = cls()
        persona_name = instance.__class__.__name__.replace('Agent', '').lower()
        userdata.personas.update({{persona_name: instance}})
        agent_instances.append(instance)
    agent_instance = agent_instances[0]
    session = AgentSession[UserData](userdata=userdata, turn_detection=EnglishModel())
    await session.start(
        agent=agent_instance,
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC())
    )
'''

AGENT_RUNTIME_CONFIG_TEMPLATE = '''{assistant_imports}
ASSISTANT_CLASSES = [{assistant_classes}]
ASSISTANT_NAMES = [{assistant_names}]
AGENT_NAME = "{agent_name}"
ENTRYPOINT_MODULE = "agent.{agent_name}.entrypoint"
ENTRYPOINT_FUNCTION = "entrypoint"
AGENT_TYPE = "OUTBOUND"
'''

ASSISTANT_TEMPLATE = '''from livekit.plugins import elevenlabs, openai, sarvam, silero
from agent.{agent_name}.assistants.base_agent import BaseAgent
from agent.{agent_name}.user.user_data import RunContext_T

class {assistant_class}(BaseAgent):
    def __init__(self):
        super().__init__(
            instructions={instructions!r},
            stt=sarvam.STT(language={stt_language!r}, model={stt_model!r}),
            llm=openai.LLM(model={llm_model!r}, temperature={llm_temperature}),
            tts=elevenlabs.TTS(
                voice_id={tts_voice_id!r},
                model={tts_model!r},
                language={tts_language!r},
                voice_settings=elevenlabs.VoiceSettings(**{tts_voice_settings})
            ),
            vad=silero.VAD.load(min_silence_duration={vad_min_silence_duration})
        )
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

# --- File Generation Logic ---
def create_outbound_agent(agent_config: dict):
    agent = agent_config["agent"]
    agent_name = agent["name"]
    assistants = agent["assistant"]
    base_path = os.path.join(os.path.dirname(__file__), "..", "agent", agent_name)
    assistants_path = os.path.join(base_path, "assistants")
    user_path = os.path.join(base_path, "user")
    logger.info(f"Creating agent directory structure for '{agent_name}' at '{base_path}'")
    os.makedirs(assistants_path, exist_ok=True)
    os.makedirs(user_path, exist_ok=True)

    # Write base_agent.py
    base_agent_path = os.path.join(assistants_path, "base_agent.py")
    logger.info(f"Writing base agent to {base_agent_path}")
    with open(base_agent_path, "w") as f:
        f.write(BASE_AGENT_TEMPLATE.format(agent_name=agent_name))
    # Write user_data.py
    user_data_path = os.path.join(user_path, "user_data.py")
    logger.info(f"Writing user data to {user_data_path}")
    with open(user_data_path, "w") as f:
        f.write(USER_DATA_TEMPLATE)

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
                agent_name=agent_name,
                assistant_class=class_name,
                instructions=assistant["prompt"],
                stt_language=assistant["stt"]["language"],
                stt_model=assistant["stt"]["model"],
                llm_model=assistant["llm"]["model"],
                llm_temperature=assistant["llm"].get("temperature", 0.3),
                tts_voice_id=assistant["tts"]["voice_id"],
                tts_model=assistant["tts"]["model"],
                tts_language=assistant["tts"]["language"],
                tts_voice_settings=assistant["tts"]["voice_settings"],
                vad_min_silence_duration=assistant["vad"].get("min_silence_duration", 0.2)
            ))

        # Write entrypoint.py
    entrypoint_path = os.path.join(base_path, "entrypoint.py")
    logger.info(f"Writing entrypoint to {entrypoint_path}")
    try:
        with open(entrypoint_path, "w") as f:
            f.write(ENTRYPOINT_TEMPLATE.format(agent_name=agent_name))
    except Exception as e:
        logger.error(f"Failed to write entrypoint.py: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise
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
    logger.info(f"Agent '{agent_name}' creation complete.")

