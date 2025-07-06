import os
import json
from dotenv import load_dotenv
from livekit import api
from livekit.agents import AgentSession, JobContext, RoomInputOptions
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.plugins import noise_cancellation
from agent.agent_inbound_v2.user.user_data import UserData
import logging
import asyncio
import json
from agent.agent_inbound_v2.agent_runtime_config import ASSISTANT_CLASSES as ASSISTANT_CLASSES_KT1

logger = logging.getLogger("kannada-tutor")
logger.setLevel(logging.INFO)

async def entrypoint(ctx: JobContext):
    
    await ctx.connect()
    
    # Check if there are already participants
    if len(ctx.room.remote_participants) == 0:
        try:
            participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=30.0)
            logger.info(f"Participant joined: {participant.identity}")
        except asyncio.TimeoutError:
            logger.warning("⏰ Timeout waiting for participant")
            return
    else:
        logger.info(f"👥 Found {len(ctx.room.remote_participants)} existing participants")

    call_source = "phone"
    caller_info = {}

    if ctx.room.metadata:
        try:
            metadata = json.loads(ctx.room.metadata)
            logger.info(f"📋 Room metadata: {metadata}")
            
            if metadata.get('source') == 'phone':
                caller_info = {
                    'from_number': metadata.get('from_number'),
                    'to_number': metadata.get('to_number'),
                    'call_uuid': metadata.get('call_uuid')
                }
                logger.info(f"📞 Detected phone call from metadata: {caller_info}")
        except Exception as e:
            logger.warning(f"❌ Error parsing room metadata: {e}")
    
    if call_source == "phone":
        logger.info("📞 Phone call detected - waiting 3 seconds for stability")
        await asyncio.sleep(3)
    else:
        await asyncio.sleep(1)
    
    userdata = UserData(
        ctx=ctx,
        call_source=call_source,
        caller_info=caller_info
    )
    agent_instances = []
    for cls in ASSISTANT_CLASSES_KT1:
        instance = cls()
        persona_name = instance.__class__.__name__.replace('Agent', '').lower()
        userdata.personas.update({persona_name: instance})
        agent_instances.append(instance)
    agent_instance = agent_instances[0]
    session = AgentSession[UserData](userdata=userdata, turn_detection=EnglishModel())
    await session.start(
        agent=agent_instance,
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC())
    )
