"""Public coordinator entrypoint; all clients execute the same LangGraph."""
from agents.workflow import ControlledWorkflow


class MasterAgent(ControlledWorkflow):
    """Intent routing, evidence collection, review, and memory coordination."""
