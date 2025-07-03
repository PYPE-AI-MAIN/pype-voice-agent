#!/bin/bash

# Exit on error
set -e

# 1. Create a virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
  python3 -m venv venv
  echo "[LOG] Virtual environment created."
else
  echo "[LOG] Virtual environment already exists."
fi

# 2. Activate the virtual environment
source venv/bin/activate
echo "[LOG] Virtual environment activated."

# 3. Export all variables from a .env file (if it exists)
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
  echo "[LOG] Environment variables exported from .env."
else
  echo "[LOG] No .env file found. Skipping environment variable export."
fi

echo "Environment variables exported and venv activated."

# 4. Run the dispatch command with the agent name from config
echo "[LOG] Running dispatch command with agent name from config..."
AGENT_NAME=$(python -c "from agent.agent_runtime_config import AGENT_NAME; print(AGENT_NAME)")
lk dispatch create --new-room --agent-name "$AGENT_NAME"