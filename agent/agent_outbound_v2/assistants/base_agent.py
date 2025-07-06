import logging
from livekit.agents import Agent
from typing import Optional
from agent.agent_outbound_v2.user.user_data import UserData, RunContext_T

logger = logging.getLogger("kannada-tutor")

class BaseAgent(Agent):
    async def on_enter(self) -> None:
        agent_name = self.__class__.__name__
        logger.info(f"Entering {agent_name}")
        userdata: UserData = self.session.userdata
        if userdata.ctx and userdata.ctx.room:
            await userdata.ctx.room.local_participant.set_attributes({"agent": agent_name})
        chat_ctx = self.chat_ctx.copy()
        if userdata.prev_agent:
            items_copy = self._truncate_chat_ctx(userdata.prev_agent.chat_ctx.items, keep_function_call=True)
            existing_ids = {item.id for item in chat_ctx.items}
            items_copy = [item for item in items_copy if item.id not in existing_ids]
            chat_ctx.items.extend(items_copy)
        chat_ctx.add_message(
            role="system",
            content=f"You are the {agent_name}. {userdata.summarize()}"
        )
        await self.update_chat_ctx(chat_ctx)
        self.session.generate_reply()

    def _truncate_chat_ctx(self, items: list, keep_last_n_messages: int = 6,
                           keep_system_message: bool = False, keep_function_call: bool = False) -> list:
        def _valid_item(item) -> bool:
            if not keep_system_message and item.type == "message" and item.role == "system":
                return False
            if not keep_function_call and item.type in ["function_call", "function_call_output"]:
                return False
            return True
        new_items = [item for item in reversed(items) if _valid_item(item)]
        new_items = new_items[:keep_last_n_messages][::-1]
        while new_items and new_items[0].type in ["function_call", "function_call_output"]:
            new_items.pop(0)
        return new_items

    async def _transfer_to_agent(self, name: str, context: RunContext_T) -> Agent:
        userdata = context.userdata
        userdata.prev_agent = context.session.current_agent
        return userdata.personas[name]
