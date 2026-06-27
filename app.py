"""
app.py
ADK app entry point for the ACC opportunity scorer.

Launch: uv run adk web app
from the opportunity-scorer directory.

The orchestrator_agent is the root agent the ADK playground connects to.
Sub-agents are instantiated by the orchestrator at runtime.
"""

from google.adk.apps.app import App
from google.adk.workflow import Workflow, node, Edge, START

from orchestrator import orchestrator_agent

# Wrap the orchestrator agent in an ADK App.
# root_agent= is the correct ADK 2.2.0 parameter (not workflow=).
app = App(root_agent=orchestrator_agent)
