import time

class TranscriptManager:
    def __init__(self, vad, trigger_callback, silence_duration: float = 0.4, min_words: int = 4):
        self.vad = vad
        self.trigger_callback = trigger_callback
        self.silence_duration = silence_duration
        self.min_words = min_words

        self.text_so_far = ""
        self.last_voice_time = time.monotonic()
        self.triggered = False

    def on_transcript_and_audio(self, transcript_text: str, audio_chunk: bytes):
        # Save partial transcript
        self.text_so_far = transcript_text

        # Voice activity = reset timer
        if self.vad.contains_voice(audio_chunk):
            self.last_voice_time = time.monotonic()

        # Already triggered = skip
        if self.triggered:
            return

        # Check silence duration + content
        if (
            (time.monotonic() - self.last_voice_time) > self.silence_duration and
            len(self.text_so_far.strip().split()) >= self.min_words
        ):
            self.triggered = True
            self.trigger_callback(self.text_so_far)

    def reset(self):
        self.text_so_far = ""
        self.last_voice_time = time.monotonic()
        self.triggered = False
