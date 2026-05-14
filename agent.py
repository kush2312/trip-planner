from langchain_groq import ChatGroq
from langchain.agents import AgentExecutor, create_react_agent
from langchain.tools import Tool
from langchain.prompts import PromptTemplate
from langchain.callbacks.base import BaseCallbackHandler
from prompts import SYSTEM_PROMPT, build_user_prompt
from tavily import TavilyClient
import asyncio
import os


# ── Tavily search tool ────────────────────────────────────────────────────────

def tavily_search(query: str) -> str:
    """Search the web using Tavily. Returns clean text results."""
    try:
        client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=5,
            include_answer=True,
        )
        parts = []
        if response.get("answer"):
            parts.append(f"Summary: {response['answer']}")
        for r in response.get("results", []):
            parts.append(f"- {r.get('title','')}: {r.get('content','')[:300]}")
        return "\n".join(parts) if parts else "No results found."
    except Exception as e:
        return f"Search failed: {str(e)}"


# ── Progress callback (called from worker thread) ─────────────────────────────

def make_progress_callback(loop, queue):
    class ProgressCallback(BaseCallbackHandler):
        def on_agent_action(self, action, **kwargs):
            msg = f"\n🔍 Searching: *{action.tool_input}*\n"
            loop.call_soon_threadsafe(queue.put_nowait, msg)

        def on_tool_end(self, output: str, **kwargs):
            loop.call_soon_threadsafe(
                queue.put_nowait, "\n✅ Got search results. Continuing...\n"
            )

    return ProgressCallback()


# ── ReAct prompt ──────────────────────────────────────────────────────────────

REACT_TEMPLATE = """You are an expert travel planner. Answer the following questions as best you can.

{system}

You have access to the following tools:

{tools}

Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat up to 6 times)
Thought: I now have enough information to write the itinerary
Final Answer: [write the full itinerary here]

Begin!

Question: {input}
Thought:{agent_scratchpad}"""


# ── Main async runner ─────────────────────────────────────────────────────────

async def run_agent(destination: str, days: int, budget: str, currency: str, style: str, queue: asyncio.Queue):
    loop = asyncio.get_event_loop()

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        groq_api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.7,
        max_tokens=4096,
        callbacks=[make_progress_callback(loop, queue)],
    )

    tools = [
        Tool(
            name="web_search",
            func=tavily_search,
            description="Search the web for travel info, attractions, hotel prices, food costs, transport, and tips.",
        )
    ]

    prompt = PromptTemplate(
        input_variables=["input", "agent_scratchpad", "tools", "tool_names"],
        partial_variables={"system": SYSTEM_PROMPT},
        template=REACT_TEMPLATE,
    )

    agent    = create_react_agent(llm=llm, tools=tools, prompt=prompt)
    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,          # prints ReAct trace to terminal for debugging
        max_iterations=8,
        handle_parsing_errors=True,
        return_intermediate_steps=False,
    )

    user_prompt = build_user_prompt(destination, days, budget, currency, style)

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(
                pool,
                lambda: executor.invoke({"input": user_prompt})
            )

        final = result.get("output", "No itinerary was generated.")
        await queue.put("\n---ITINERARY_START---\n")
        await queue.put(final)
        await queue.put("\n---ITINERARY_END---\n")

    except Exception as e:
        await queue.put(f"\n\n❌ Error: {str(e)}")

    finally:
        await queue.put(None)