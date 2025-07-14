ASSISTANT_TEMPLATE = '''from livekit.plugins import elevenlabs, openai, sarvam, silero
from base_agent import BaseAgent
from user_data import RunContext_T

class {name}(BaseAgent):
    def __init__(self):
        super().__init__(
            instructions={prompt!r},
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