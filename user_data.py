from dataclasses import dataclass, field
from typing import Optional
from livekit.agents import Agent, JobContext, RunContext

@dataclass
class UserData:
    personas: dict = field(default_factory=dict)
    prev_agent: Optional[Agent] = None
    ctx: Optional[JobContext] = None
    call_source: str = None
    caller_info: dict = None

    def summarize(self) -> str:
        return "User data: user voice agent"

RunContext_T = RunContext[UserData]
