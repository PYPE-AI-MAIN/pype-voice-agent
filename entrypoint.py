import json
from dotenv import load_dotenv
from livekit.agents import AgentSession, RoomInputOptions
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.plugins import noise_cancellation
from livekit import api
import os
import importlib
from user_data import UserData
import logging
from collections import defaultdict

load_dotenv()

def get_assistant_classes(agent_name):
    module_path = f"agent.{agent_name}.agent_runtime_config"
    module = importlib.import_module(module_path)
    return getattr(module, "ASSISTANT_CLASSES")

async def entrypoint(ctx):
    print("[Agent Entrypoint] Called with ctx:", ctx)

    # 1. Get room name and metadata from job context
    room_name = getattr(ctx, "room", None)
    metadata = None
    if hasattr(ctx, "job") and ctx.job and hasattr(ctx.job, "metadata"):
        metadata = ctx.job.metadata
    print(f"[Agent Entrypoint] room_name={room_name}, metadata={metadata}")

    # 2. Parse metadata
    meta = {}
    if metadata:
        try:
            print(f"[Agent Entrypoint] Raw metadata: {metadata}")
            meta = json.loads(metadata)
        except Exception as e:
            print(f"[Agent Entrypoint] ERROR: Could not parse metadata: {e}")

    # 2.1. Get agent_name from metadata (default to 'customer_support_specialist_v2')
    agent_name = meta.get("agent_name", "customer_support_specialist")
    ASSISTANT_CLASSES = get_assistant_classes(agent_name)

    # Set up logging and metrics
    logger = logging.getLogger("agent-entrypoint")
    logger.setLevel(logging.INFO)
    try:
        from livekit.agents import metrics, MetricsCollectedEvent
    except ImportError:
        metrics = None
        MetricsCollectedEvent = None
    usage_collector = metrics.UsageCollector() if metrics else None
    latency_metrics = defaultdict(dict)

    def setup_metrics(session):
        if not metrics:
            return
        @session.on("metrics_collected")
        def _on_metrics_collected(ev: MetricsCollectedEvent):
            metrics.log_metrics(ev.metrics)
            usage_collector.collect(ev.metrics)
            m = ev.metrics
            metric_type = type(m).__name__
            speech_id = getattr(m, "speech_id", None)
            if speech_id:
                latency_metrics[speech_id][metric_type] = m
                if all(k in latency_metrics[speech_id] for k in ("EOUMetrics", "LLMMetrics", "TTSMetrics")):
                    eou = latency_metrics[speech_id]["EOUMetrics"]
                    llm = latency_metrics[speech_id]["LLMMetrics"]
                    tts = latency_metrics[speech_id]["TTSMetrics"]
                    total_latency = eou.end_of_utterance_delay + llm.ttft + tts.ttfb
                    logger.info(
                        f"Turn {speech_id} Latency: EOU={eou.end_of_utterance_delay:.3f}s, "
                        f"LLM TTFT={llm.ttft:.3f}s, TTS TTFB={tts.ttfb:.3f}s, "
                        f"Total={total_latency:.3f}s"
                    )
        async def log_usage():
            summary = usage_collector.get_summary()
            logger.info(f"Usage: {summary}")
        ctx.add_shutdown_callback(log_usage)

    # 3. Decide flow based on metadata
    if meta.get("source") == "web":
        print("[Agent Entrypoint] Handling web session")
        await ctx.connect()
        room = ctx.room
        userdata = UserData(ctx=ctx)
        agent_instances = []
        for cls in ASSISTANT_CLASSES:
            instance = cls()
            persona_name = instance.__class__.__name__.replace('Agent', '').lower()
            userdata.personas.update({persona_name: instance})
            agent_instances.append(instance)
        print(f"[Agent Entrypoint] Created {len(agent_instances)} agent instances (web)")
        agent_instance = agent_instances[0]
        session = AgentSession[UserData](userdata=userdata, turn_detection=EnglishModel())
        setup_metrics(session)
        print("[Agent Entrypoint] Starting agent session in web flow")
        await session.start(
            agent=agent_instance,
            room=room,
            room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC())
        )
        print("[Agent Entrypoint] Agent session started and running in web flow")
        return

    if meta.get("source") == "outbound":
        print("[Agent Entrypoint] Handling SIP/outbound flow")
        await ctx.connect()
        outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
        try:
            print("[Agent Entrypoint] Parsing dial_info from metadata")
            dial_info = meta
            phone_number = dial_info.get("phone_number", None)
            print(f"[Agent Entrypoint] Parsed phone_number: {phone_number}")
        except Exception as e:
            print(f"[Agent Entrypoint] ERROR: Exception parsing dial_info: {e}")
            phone_number = None
        print(f"[Agent Entrypoint] Outbound trunk ID: {outbound_trunk_id}, phone_number: {phone_number}")
        try:
            print("[Agent Entrypoint] Creating SIP participant")
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=outbound_trunk_id,
                    sip_call_to=phone_number,
                    participant_identity=phone_number,
                    wait_until_answered=True,
                )
            )
            print("[Agent Entrypoint] SIP participant created")
        except Exception as e:
            print(f"[Agent Entrypoint] ERROR: Exception creating SIP participant: {e}")
        try:
            print("[Agent Entrypoint] Preparing UserData and agent instances for outbound flow")
            userdata = UserData(ctx=ctx)
            agent_instances = []
            for cls in ASSISTANT_CLASSES:
                instance = cls()
                persona_name = instance.__class__.__name__.replace('Agent', '').lower()
                userdata.personas.update({persona_name: instance})
                agent_instances.append(instance)
            print(f"[Agent Entrypoint] Created {len(agent_instances)} agent instances (outbound)")
            agent_instance = agent_instances[0]
            session = AgentSession[UserData](userdata=userdata, turn_detection=EnglishModel())
            setup_metrics(session)
            print("[Agent Entrypoint] Starting agent session in outbound SIP flow")
            await session.start(
                agent=agent_instance,
                room=ctx.room,
                room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC())
            )
            print("[Agent Entrypoint] Agent session started and running in outbound SIP flow")
        except Exception as e:
            print(f"[Agent Entrypoint] ERROR: Exception during agent session in outbound SIP flow: {e}")
        print("[Agent Entrypoint] Exiting outbound SIP flow")
        return

    # Fallback: phone call logic (from tutors/entrypoint.py)
    print("[Agent Entrypoint] Fallback: handling as phone call")
    import asyncio
    await ctx.connect()
    call_source = "phone"
    caller_info = {}
    room_metadata = getattr(ctx.room, 'metadata', None)
    if room_metadata:
        try:
            metadata = json.loads(room_metadata)
            print(f"[Agent Entrypoint] 📋 Room metadata: {metadata}")
            if metadata.get('source') == 'phone':
                caller_info = {
                    'from_number': metadata.get('from_number'),
                    'to_number': metadata.get('to_number'),
                    'call_uuid': metadata.get('call_uuid')
                }
                print(f"[Agent Entrypoint] 📞 Detected phone call from metadata: {caller_info}")
        except Exception as e:
            print(f"[Agent Entrypoint] ❌ Error parsing room metadata: {e}")
    print("[Agent Entrypoint] 📞 Phone call detected - waiting 3 seconds for stability")
    await asyncio.sleep(3)
    userdata = UserData(
        ctx=ctx,
        call_source=call_source,
        caller_info=caller_info
    )
    agent_instances = []
    for cls in ASSISTANT_CLASSES:
        instance = cls()
        persona_name = instance.__class__.__name__.replace('Agent', '').lower()
        userdata.personas.update({persona_name: instance})
        agent_instances.append(instance)
    agent_instance = agent_instances[0]
    session = AgentSession[UserData](userdata=userdata, turn_detection=EnglishModel())
    setup_metrics(session)
    await session.start(
        agent=agent_instance,
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC())
    )
    return