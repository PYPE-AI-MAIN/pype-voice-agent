import logging
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, HTTPException, Body, Request
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import yaml
import os, random, datetime
import subprocess
import glob
import importlib.util
import re
import signal
import json
from utils.create_outbound_agent import create_outbound_agent, AgentRequest
from utils.create_agent import create_agent, AgentRequest
from fastapi.middleware.cors import CORSMiddleware
import sys
python_executable = sys.executable
from fastapi.responses import JSONResponse
from livekit import api
from livekit.api.access_token import VideoGrants
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional


# Load environment variables from .env at startup
load_dotenv()

app = FastAPI()

# Enable CORS

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://vc-agent-dashboard-j119.vercel.app",
        "http://localhost:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


PID_DIR = "pids"
os.makedirs(PID_DIR, exist_ok=True)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("server")

class AssistantConfig(BaseModel):
    name: str
    prompt: str
    stt: Dict[str, Any]
    llm: Dict[str, Any]
    tts: Dict[str, Any]
    vad: Optional[Dict[str, Any]]

class AgentConfig(BaseModel):
    name: str
    assistant: List[AssistantConfig]

class RootConfig(BaseModel):
    agent: AgentConfig

@app.post("/config")
def create_config(config: RootConfig):
    yaml_path = os.path.join(os.path.dirname(__file__), "agent-config.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(config.dict(), f, allow_unicode=True, sort_keys=False)
    # Write agent_config.json inside the agent's folder
    agent_name = config.agent.name
    agent_dir = os.path.join(os.path.dirname(__file__), "agent", agent_name)
    os.makedirs(agent_dir, exist_ok=True)
    json_path = os.path.join(agent_dir, "agent_config.json")
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(config.dict(), jf, indent=2, ensure_ascii=False)
    return {"status": "success", "yaml_path": yaml_path, "json_path": json_path}

class RunAgentRequest(BaseModel):
    agent_name: str
    room_name: Optional[str] = None
    agent_token: Optional[str] = None
    agent_identity: Optional[str] = None

# Find all agent config files
AGENT_CONFIGS = glob.glob("agent/*/agent_runtime_config.py")

def get_agent_name(config_path):
    with open(config_path, "r") as f:
        content = f.read()
    match = re.search(r'AGENT_NAME\s*=\s*[\'"]([^\'"]+)[\'"]', content)
    if match:
        return match.group(1)
    return os.path.basename(os.path.dirname(config_path))

def get_agent_type(config_path):
    with open(config_path, "r") as f:
        content = f.read()
    match = re.search(r'AGENT_TYPE\s*=\s*[\'\"]([^\'\"]+)[\'\"]', content)
    if match:
        return match.group(1)
    return None

@app.get("/agents")
def list_agents():
    return [
        {"name": get_agent_name(cfg), "config_path": cfg, "type": get_agent_type(cfg)}
        for cfg in AGENT_CONFIGS
    ]

@app.get("/running_agents")
def running_agents():
    agents = []
    for pid_file in os.listdir(PID_DIR):
        if pid_file.endswith(".pid"):
            agent_name = pid_file[:-4]
            with open(os.path.join(PID_DIR, pid_file), "r") as f:
                pid = int(f.read())
            # Check if process is alive
            try:
                os.kill(pid, 0)
                agents.append({"agent_name": agent_name, "pid": pid})
            except ProcessLookupError:
                os.remove(os.path.join(PID_DIR, pid_file))  # Clean up stale PID file
    return agents

@app.post("/run_agent")
def run_agent(req: RunAgentRequest):
    # Find config path by agent_name
    config_path = None
    for cfg in AGENT_CONFIGS:
        if get_agent_name(cfg) == req.agent_name:
            config_path = cfg
            break
    if not config_path:
        raise HTTPException(status_code=404, detail="Agent not found")
    pid_file = os.path.join(PID_DIR, f"{req.agent_name}.pid")
    # If already running, don't start again
    if os.path.exists(pid_file):
        with open(pid_file, "r") as f:
            pid = int(f.read())
        # Optionally, check if process is still alive
        try:
            os.kill(pid, 0)
            return {"status": "already running", "pid": pid}
        except ProcessLookupError:
            os.remove(pid_file)  # Stale PID file
    env = os.environ.copy()
    env["AGENT_CONFIG_PATH"] = config_path
    # Set web session variables if provided
    if req.room_name:
        env["ROOM_NAME"] = req.room_name
    if req.agent_token:
        env["AGENT_TOKEN"] = req.agent_token
    if req.agent_identity:
        env["AGENT_IDENTITY"] = req.agent_identity
    proc = subprocess.Popen(
        [python_executable, "main.py", "dev"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=env,
    )
    with open(pid_file, "w") as f:
        f.write(str(proc.pid))
    return {"status": "started", "pid": proc.pid}

class StopAgentRequest(BaseModel):
    agent_name: str

@app.post("/stop_agent")
def stop_agent(req: StopAgentRequest):
    pid_file = os.path.join(PID_DIR, f"{req.agent_name}.pid")
    if not os.path.exists(pid_file):
        raise HTTPException(status_code=404, detail="Agent not running (no PID file)")
    with open(pid_file, "r") as f:
        pid = int(f.read())
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # Process already stopped
    os.remove(pid_file)
    return {"status": "stopped"}

@app.post("/force_stop_agent")
def force_stop_agent(req: StopAgentRequest):
    pid_file = os.path.join(PID_DIR, f"{req.agent_name}.pid")
    if not os.path.exists(pid_file):
        raise HTTPException(status_code=404, detail="Agent not running (no PID file)")
    with open(pid_file, "r") as f:
        pid = int(f.read())
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # Process already stopped
    os.remove(pid_file)
    return {"status": "force killed"}

@app.post("/set-env")
def set_env(vars: Dict[str, str]):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    existing = {}

    # Read existing .env if it exists
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                existing[k] = v

    # Update with new vars
    existing.update(vars)

    # Write back to .env
    with open(env_path, "w") as f:
        for k, v in existing.items():
            f.write(f"{k}={v}\n")

    return {"status": "env merged", "written_to": env_path, "vars": vars}

class DispatchRequest(BaseModel):
    agent_name: str
    phone_number: str

@app.post("/dispatch_call")
def dispatch_call(req: DispatchRequest):
    logger.info(f"Starting dispatch for agent: {req.agent_name}, phone_number: {req.phone_number}")
    metadata = json.dumps({"phone_number": req.phone_number, "source": "outbound", "agent_name": req.agent_name})
    env = os.environ.copy()  # Ensure all current env vars (including API keys) are passed
    # Log relevant env vars for debugging
    logger.info(f"LIVEKIT_API_KEY: {env.get('LIVEKIT_API_KEY')}")
    logger.info(f"LIVEKIT_API_SECRET: {env.get('LIVEKIT_API_SECRET')}")
    logger.info(f"LIVEKIT_URL: {env.get('LIVEKIT_URL')}")
    command = [
        "lk", "dispatch", "create",
        "--new-room",
        "--agent-name", req.agent_name,
        "--metadata", metadata
    ]
    logger.info(f"Running command: {' '.join(command)}")
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    logger.info(f"lk CLI stdout: {result.stdout}")
    if result.stderr:
        logger.error(f"lk CLI stderr: {result.stderr}")
    if result.returncode != 0:
        logger.error(f"lk CLI failed with return code {result.returncode}")
        raise HTTPException(status_code=500, detail=result.stderr)
    logger.info(f"Dispatch for agent {req.agent_name} completed successfully.")
    return {"status": "dispatched", "output": result.stdout}
    
@app.post("/create-agent")
def create_agent_endpoint(request: AgentRequest):
    logger.info("Received request to create inbound agent.")
    try:
        create_agent(request.dict())
        logger.info("Inbound agent creation successful.")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Agent creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class CreateDispatchRuleRequest(BaseModel):
    trunk_id: str
    room_prefix: str = "call-"



@app.post("/create_dispatch_rule")
def create_dispatch_rule(req: CreateDispatchRuleRequest):
    import subprocess
    command = [
        "lk", "sip", "dispatch", "create",
        "--trunks", req.trunk_id,
        "--individual", req.room_prefix
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Failed to create dispatch rule: {result.stderr}")
    # Try to parse dispatch rule ID from output
    import json
    try:
        dispatch_result = json.loads(result.stdout)
        dispatch_rule_id = dispatch_result.get("id") or dispatch_result.get("sipDispatchRuleId")
    except Exception:
        dispatch_rule_id = None
    return {
        "dispatch_rule_id": dispatch_rule_id,
        "dispatch_cli_output": result.stdout
    }

import uuid

@app.post("/start_web_session")
async def dispatch_web_session(request: Request):
    data = await request.json()
    agent_name = data["agent_name"]
    user_identity = f"user_identity-{uuid.uuid4()}"
    user_name = f"user_name-{uuid.uuid4()}"

    # 1. Generate unique room name
    room_name = f"web-{uuid.uuid4()}"

    # 2. Generate user token for the frontend
    user_token = api.AccessToken(
        os.getenv('LIVEKIT_API_KEY'), os.getenv('LIVEKIT_API_SECRET')
    ).with_identity(user_identity).with_name(user_name).with_grants(
        api.VideoGrants(room_join=True, room=room_name)
    ).to_jwt()

    # 3. Create a dispatch job for the agent using the LiveKit CLI
    metadata = json.dumps({
        "source": "web",
        "user_identity": user_identity,
        "user_name": user_name,
        "agent_name": agent_name
    })
    env = os.environ.copy()
    command = [
        "lk", "dispatch", "create",
        "--agent-name", agent_name,
        "--room", room_name,
        "--metadata", metadata
    ]
    result = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    for line in result.stdout:
        print("DISPATCH STDOUT:", line, end="")
    for line in result.stderr:
            print("DISPATCH STDERR:", line, end="")
    result.wait()
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Failed to dispatch web session: {result.stderr}")

    return {
        "room": room_name,
        "user_token": user_token,
        "agent_name": agent_name,
        "dispatch_cli_output": result.stdout
    }

class CreateSIPDispatchRuleRequestModel(BaseModel):
    room_prefix: str = "call-"
    agent_name: str
    metadata: Optional[str] = None

class CreateSIPDispatchRuleRequestModel(BaseModel):
    room_prefix: str = "call-"
    agent_name: str
    metadata: Optional[str] = None
    trunkIds: Optional[List[str]] = None
    name: Optional[str] = None

@app.post("/create_sip_dispatch_rule")
async def create_sip_dispatch_rule(request: CreateSIPDispatchRuleRequestModel):
    from livekit import api
    lkapi = api.LiveKitAPI()
    req = api.CreateSIPDispatchRuleRequest(
        rule=api.SIPDispatchRule(
            dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                room_prefix=request.room_prefix,
            )
        ),
        room_config=api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(
                agent_name=request.agent_name,
                metadata=request.metadata or ""
            )]
        ),
        trunk_ids=request.trunkIds or [],
        name=request.name or ""
    )
    dispatch = await lkapi.sip.create_sip_dispatch_rule(req)
    await lkapi.aclose()
    return {"dispatch": dispatch.to_dict() if hasattr(dispatch, 'to_dict') else str(dispatch)}


@app.delete("/delete_sip_trunk/{trunk_id}")
async def delete_sip_trunk(trunk_id: str):
    from livekit import api
    lkapi = api.LiveKitAPI()
    try:
        result = await lkapi.sip.delete_sip_trunk(trunk_id)
        await lkapi.aclose()
        return {"status": "deleted", "trunk_id": trunk_id, "result": str(result)}
    except Exception as e:
        await lkapi.aclose()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/delete_sip_dispatch_rule/{dispatch_rule_id}")
async def delete_sip_dispatch_rule(dispatch_rule_id: str):
    from livekit import api
    lkapi = api.LiveKitAPI()
    try:
        result = await lkapi.sip.delete_sip_dispatch_rule(dispatch_rule_id)
        await lkapi.aclose()
        return {"status": "deleted", "dispatch_rule_id": dispatch_rule_id, "result": str(result)}
    except Exception as e:
        await lkapi.aclose()
        raise HTTPException(status_code=500, detail=str(e))

class CreateSIPInboundTrunkRequest(BaseModel):
    name: str
    numbers: List[str]
    allowed_numbers: List[str] = []

@app.post("/create_sip_inbound_trunk")
async def create_sip_inbound_trunk(request: CreateSIPInboundTrunkRequest):
    from livekit import api
    from livekit.protocol.sip import SIPInboundTrunkInfo
    lkapi = api.LiveKitAPI()
    trunk_info = SIPInboundTrunkInfo(
        numbers=request.numbers,
        allowed_numbers=request.allowed_numbers,
        name=request.name
    )
    trunk_request = api.CreateSIPInboundTrunkRequest(trunk=trunk_info)
    trunk = await lkapi.sip.create_sip_inbound_trunk(trunk_request)
    await lkapi.aclose()
    return {"trunk": trunk.to_dict() if hasattr(trunk, 'to_dict') else str(trunk)}

@app.get("/list_sip_inbound_trunks")
async def list_sip_inbound_trunks():
    from livekit import api
    from livekit.protocol.sip import ListSIPInboundTrunkRequest
    import re
    lkapi = api.LiveKitAPI()
    trunks = await lkapi.sip.list_sip_inbound_trunk(ListSIPInboundTrunkRequest())
    await lkapi.aclose()
    # Convert to dict if possible
    if hasattr(trunks, 'to_dict'):
        return {"trunks": trunks.to_dict()}
    # If trunks is a string, parse it into a list of dicts
    trunks_str = str(trunks)
    items = []
    for item_str in re.split(r'items \{', trunks_str):
        item_str = item_str.strip().strip('}').strip()
        if not item_str:
            continue
        trunk = {}
        for line in item_str.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                trunk[key.strip()] = value.strip().strip('"')
        if trunk:
            items.append(trunk)
    return {"trunks": items}

@app.get("/list_sip_dispatch_rules")
async def list_sip_dispatch_rules():
    from livekit import api
    import re
    lkapi = api.LiveKitAPI()
    rules = await lkapi.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
    await lkapi.aclose()
    # Convert to dict if possible
    if hasattr(rules, 'to_dict'):
        return {"dispatch_rules": rules.to_dict()}
    # If rules is a string, parse it into a list of dicts
    rules_str = str(rules)
    items = []
    for item_str in re.split(r'items \{', rules_str):
        item_str = item_str.strip().strip('}').strip()
        if not item_str:
            continue
        rule = {}
        for line in item_str.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                rule[key.strip()] = value.strip().strip('"')
        if rule:
            items.append(rule)
    return {"dispatch_rules": items}

@app.get("/dispatch_rule_numbers")
async def dispatch_rule_numbers():
    from fastapi import Request
    from livekit import api
    import re
    lkapi = api.LiveKitAPI()
    # Get trunks
    trunks_resp = await lkapi.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
    # Get dispatch rules
    rules_resp = await lkapi.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
    await lkapi.aclose()
    # Parse trunks
    if hasattr(trunks_resp, 'to_dict'):
        trunks = trunks_resp.to_dict().get('items', [])
    else:
        trunks = []
        trunks_str = str(trunks_resp)
        for item_str in re.split(r'items \{', trunks_str):
            item_str = item_str.strip().strip('}').strip()
            if not item_str:
                continue
            trunk = {}
            for line in item_str.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    trunk[key.strip()] = value.strip().strip('"')
            if trunk:
                trunks.append(trunk)
    # Parse dispatch rules
    if hasattr(rules_resp, 'to_dict'):
        rules = rules_resp.to_dict().get('items', [])
    else:
        rules = []
        rules_str = str(rules_resp)
        for item_str in re.split(r'items \{', rules_str):
            item_str = item_str.strip().strip('}').strip()
            if not item_str:
                continue
            rule = {}
            for line in item_str.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    rule[key.strip()] = value.strip().strip('"')
            if rule:
                rules.append(rule)
    # Build mapping trunk_id -> numbers
    trunk_id_to_numbers = {}
    for trunk in trunks:
        trunk_id = trunk.get('sip_trunk_id')
        numbers = trunk.get('numbers')
        if trunk_id and numbers:
            trunk_id_to_numbers[trunk_id] = numbers
    # Build result: for each dispatch rule, get its trunk_ids and map to numbers
    result = []
    for rule in rules:
        rule_id = rule.get('sip_dispatch_rule_id')
        trunk_ids = rule.get('trunk_ids')
        agent_name = rule.get('agent_name')
        # trunk_ids may be a comma-separated string
        if trunk_ids:
            trunk_id_list = [tid.strip() for tid in trunk_ids.split(',')]
            numbers = [trunk_id_to_numbers.get(tid) for tid in trunk_id_list if tid in trunk_id_to_numbers]
        else:
            trunk_id_list = []
            numbers = []
        result.append({
            'dispatch_rule_id': rule_id,
            'numbers': numbers,
            'agent_name': agent_name,
            'sip_trunk_id': trunk_id_list
        })
    return {'dispatch_rule_numbers': result}

class ReplaceDispatchRuleRequest(BaseModel):
    dispatch_rule_id: str
    room_prefix: str = "call-"
    agent_name: str
    metadata: str
    trunkIds: list
    name: str

@app.post("/replace_dispatch_rule")
async def replace_dispatch_rule(request: ReplaceDispatchRuleRequest):
    from livekit import api
    from livekit.protocol.sip import DeleteSIPDispatchRuleRequest
    lkapi = api.LiveKitAPI()
    try:
        delete_request = DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=request.dispatch_rule_id)
        await lkapi.sip.delete_sip_dispatch_rule(delete_request)
       
        # 2. Create the new dispatch rule
        agent_obj = api.RoomAgentDispatch(
            agent_name=request.agent_name,
            metadata=str(request.metadata)  # ensure this is a string
        )
        room_config_obj = api.RoomConfiguration(
            agents=[agent_obj]
        )
        req = api.CreateSIPDispatchRuleRequest(
            rule=api.SIPDispatchRule(
                dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                    room_prefix=request.room_prefix,
                )
            ),
            room_config=room_config_obj,
            trunk_ids=request.trunkIds,
            name=request.name
        )
        dispatch = await lkapi.sip.create_sip_dispatch_rule(req)
        await lkapi.aclose()
        return {"dispatch": dispatch.to_dict() if hasattr(dispatch, 'to_dict') else str(dispatch)}
    except Exception as e:
        await lkapi.aclose()
        raise HTTPException(status_code=500, detail=str(e))
