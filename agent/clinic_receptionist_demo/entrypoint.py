import os
import json
from dotenv import load_dotenv
from livekit import api
from livekit.agents import AgentSession, JobContext, RoomInputOptions, metrics, MetricsCollectedEvent
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.plugins import noise_cancellation
from agent.clinic_receptionist_demo.user.user_data import UserData
from agent.clinic_receptionist_demo.agent_runtime_config import ASSISTANT_CLASSES as ASSISTANT_CLASSES_KT1
import logging
from collections import defaultdict

load_dotenv()


logger = logging.getLogger("outbound-caller")
logger.setLevel(logging.INFO)

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
        userdata.personas.update({persona_name: instance})
        agent_instances.append(instance)
    agent_instance = agent_instances[0]
    session = AgentSession[UserData](userdata=userdata, turn_detection=EnglishModel())
    await session.start(
        agent=agent_instance,
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC())
    )

    usage_collector = metrics.UsageCollector()
    # Store metrics by speech_id for latency calculation
    latency_metrics = defaultdict(dict)

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

        m = ev.metrics
        metric_type = type(m).__name__
        speech_id = getattr(m, "speech_id", None)

        # Store metrics for this speech_id
        if speech_id:
            latency_metrics[speech_id][metric_type] = m

            # If we have all three types, compute and log latency
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

    # At shutdown, generate and log the summary from the usage collector
    ctx.add_shutdown_callback(log_usage)
