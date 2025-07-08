import logging
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, HTTPException, Body
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import yaml
import os
import subprocess
import glob
import importlib.util
import re
import signal
import json
from utils.create_outbound_agent import create_outbound_agent, AgentRequest
from utils.create_inbound_agent import create_inbound_agent, AgentRequest
from fastapi.middleware.cors import CORSMiddleware
import sys
python_executable = sys.executable


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
    proc = subprocess.Popen(
        [python_executable, "main.py", "dev"], #this would run subprocess in virtual mode too
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
    metadata = json.dumps({"phone_number": req.phone_number})
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

@app.post("/create-outbound-agent")
def create_agent_endpoint(request: AgentRequest):
    try:
        create_outbound_agent(request.dict())
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/create-inbound-agent/")
def create_inbound_agent_endpoint(request: AgentRequest):
    logger.info("Received request to create inbound agent.")
    try:
        create_inbound_agent(request.dict())
        logger.info("Inbound agent creation successful.")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Inbound agent creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class CreateInboundTrunkRequest(BaseModel):
    trunk_name: str
    numbers: list[str]

class CreateDispatchRuleRequest(BaseModel):
    trunk_id: str
    room_prefix: str = "call-"

@app.post("/create_inbound_trunk")
def create_inbound_trunk(req: CreateInboundTrunkRequest):
    import subprocess, json
    trunk_json = {
        "trunk": {
            "name": req.trunk_name,
            "numbers": req.numbers
        }
    }
    trunk_json_path = os.path.join(os.path.dirname(__file__), "inbound-trunk.json")
    with open(trunk_json_path, "w") as f:
        json.dump(trunk_json, f, indent=2)
    # Run lk sip inbound create
    result = subprocess.run(["lk", "sip", "inbound", "create", trunk_json_path], capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Failed to create inbound trunk: {result.stderr}")
    # Parse trunk ID from output (assume JSON output)
    try:
        trunk_result = json.loads(result.stdout)
        trunk_id = trunk_result.get("id") or trunk_result.get("trunkId")
    except Exception:
        # Fallback: try to extract trunk id from text
        import re
        match = re.search(r'([A-Z0-9_\-]{10,})', result.stdout)
        trunk_id = match.group(1) if match else None
    if not trunk_id:
        raise HTTPException(status_code=500, detail="Could not parse trunk ID from CLI output")
    return {
        "trunk_id": trunk_id,
        "trunk_cli_output": result.stdout
    }

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