from livekit.plugins import elevenlabs, openai, sarvam, silero
from agent.agent_inbound_v2.assistants.base_agent import BaseAgent
from agent.agent_inbound_v2.user.user_data import RunContext_T

class Neha(BaseAgent):
    def __init__(self):
        super().__init__(
            instructions='You are a good agent',
            stt=sarvam.STT(language='hindi', model='saarika:v2.5'),
            llm=openai.LLM(model='gpt-4.1-mini', temperature=0.3),
            tts=elevenlabs.TTS(
                voice_id='H8bdWZHK2OgZwTN7ponr',
                model='eleven_flash_v2_5',
                language='hi',
                voice_settings=elevenlabs.VoiceSettings(**{'similarity_boost': 1.0, 'stability': 0.7, 'style': 0.7, 'use_speaker_boost': False, 'speed': 1.1})
            ),
            vad=silero.VAD.load(min_silence_duration=0.2)
        )
