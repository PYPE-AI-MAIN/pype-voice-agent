#!/bin/bash

# Exit on error
set -e

# 2. Create a virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
  python3 -m venv venv
  echo "[LOG] Virtual environment created."
else
  echo "[LOG] Virtual environment already exists."
fi

# 3. Activate the virtual environment
source venv/bin/activate
echo "[LOG] Virtual environment activated."

# 1. Export all variables from a .env file (if it exists)
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
  echo "[LOG] Environment variables exported from .env."
else
  echo "[LOG] No .env file found. Skipping environment variable export."
fi

# 4. (Optional) Install requirements
pip install -r requirements.txt
echo "[LOG] Requirements installed."

# 5. Your script logic here 
echo "Environment variables exported and venv activated."

# Run outbound trunk creation and capture output
# OUTBOUND_RESPONSE=$(lk sip outbound create outbound-trunk.json)

# # Extract SIPTrunkID value
# SIP_OUTBOUND_TRUNK_ID=$(echo "$OUTBOUND_RESPONSE" | grep -oE 'SIPTrunkID: [^ ]+' | awk '{print $2}')

# export SIP_OUTBOUND_TRUNK_ID
# echo "[LOG] Exported SIP_OUTBOUND_TRUNK_ID=$SIP_OUTBOUND_TRUNK_ID"

# 5. Generate agent folder and Python file from YAML config

python3 <<EOF
import os
import yaml

with open('agent-config.yaml') as f:
    config = yaml.safe_load(f)

agent = config['agent']
name = agent['name']
folder = f"agent_{name}"
folder_path = os.path.join('agent', folder)
os.makedirs(folder_path, exist_ok=True)
print(f"[LOG] Created folder: {folder_path}")

# Prepare agent Python file content
def py_bool(val):
    return 'True' if val else 'False'

py_content = f'''
from livekit.plugins import silero, openai, sarvam
from base_agent import BaseAgent

class {name.capitalize()}Agent(BaseAgent):
    def __init__(self):
        super().__init__(
            instructions={repr(agent['prompt'])},
            stt=sarvam.STT(language={repr(agent['stt']['language'])}, model={repr(agent['stt']['model'])}),
            llm=openai.LLM(model={repr(agent['llm']['model'])}),
            tts=sarvam.TTS(
                target_language_code={repr(agent['tts']['target_language_code'])},
                model={repr(agent['tts']['model'])},
                speaker={repr(agent['tts']['speaker'])},
                pitch={agent['tts']['pitch']},
                pace={agent['tts']['pace']},
                loudness={agent['tts']['loudness']},
                enable_preprocessing={py_bool(agent['tts']['enable_preprocessing'])}
            ),
            vad=silero.VAD.load(),
        )
'''

with open(os.path.join(folder_path, f"agent_{name}.py"), 'w') as f:
    f.write(py_content)

print(f"[LOG] Generated {folder_path}/agent_{name}.py")
EOF




python3 <<EOF
import os
import yaml

with open('agent-config.yaml') as f:
    config = yaml.safe_load(f)

agent = config['agent']
name = agent['name']
phone_number = config['phone_number']

runtime_config = f'''
PHONE_NUMBER = {repr(phone_number)}

from agent.agent_{name}.agent_{name} import {name.capitalize()}Agent
AGENT_CLASS = {name.capitalize()}Agent
AGENT_NAME = {repr(name)}
'''

with open('agent/agent_runtime_config.py', 'w') as f:
    f.write(runtime_config)

print(f"[LOG] Generated agent/agent_runtime_config.py")
EOF

echo "[LOG] Script completed successfully."

# 6. Run the main file in dev mode in the background
python -m main dev &
SERVER_PID=$!
echo "[LOG] Server started with PID $SERVER_PID."

# 7. Wait a few seconds for the server to start (optional, adjust as needed)
sleep 5

# 8. Run the dispatch command with the agent name from config
echo "[LOG] Running dispatch command with agent name from config..."
AGENT_NAME=$(python -c "from agent.agent_runtime_config import AGENT_NAME; print(AGENT_NAME)")
# lk dispatch create --new-room --agent-name "$AGENT_NAME"

# 9. Wait for the server process to finish
wait $SERVER_PID

