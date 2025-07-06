from dataclasses import dataclass, field
from typing import Optional
from livekit.agents import Agent, JobContext, RunContext

@dataclass
class UserData:
    personas: dict = field(default_factory=dict)
    prev_agent: Optional[Agent] = None
    ctx: Optional[JobContext] = None
    current_lesson_step: str = "greeting"
    teaching_context: str = ""
    call_source: str = "unknown"  # Track if call is from phone, web, etc.
    caller_info: dict = field(default_factory=dict)  # Store caller information
    
    def summarize(self) -> str:
        return f"User data: Helpful voice agent - Step: {{self.current_lesson_step}}, Source: {{self.call_source}}"

RunContext_T = RunContext[UserData]
