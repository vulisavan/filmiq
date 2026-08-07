"""
filmiq_app.py
ADK app entry point for filmIQ.
Launch: uv run adk web filmiq_app
from the filmiq directory.
The orchestrator_agent is the root agent the ADK playground connects to.
Sub-agents are instantiated by the orchestrator at runtime.
"""
from google.adk.apps.app import App
from orchestrator import orchestrator_agent

# Wrap the orchestrator agent in an ADK App.
# root_agent= is the correct ADK 2.2.0 parameter (not workflow=).
app = App(root_agent=orchestrator_agent)
