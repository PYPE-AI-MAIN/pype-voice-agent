import os
import json
from dotenv import load_dotenv
from livekit import api
from livekit.agents import AgentSession, JobContext, RoomInputOptions
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.plugins import noise_cancellation
from user_data import UserData
from agent.agent_runtime_config import PHONE_NUMBER, AGENT_CLASS

load_dotenv()

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
    try:
        dial_info = json.loads(ctx.job.metadata)
        phone_number = dial_info.get("phone_number", PHONE_NUMBER)
    except Exception:
        phone_number = PHONE_NUMBER

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
    agent_instance = AGENT_CLASS()
    # The persona name is set to the agent class name (lowercased, without 'Agent' suffix)
    persona_name = agent_instance.__class__.__name__.replace('Agent', '').lower()
    userdata.personas.update({persona_name: agent_instance})

    session = AgentSession[UserData](userdata=userdata, turn_detection=EnglishModel())
    await session.start(
        agent=agent_instance,
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC())
    )