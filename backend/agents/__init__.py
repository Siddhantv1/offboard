"""Importing this package registers all agents into REGISTRY (import order = UI order)."""
from .base import REGISTRY, AgentMeta, BaseAgent, Field  # noqa: F401

# Import each agent module so its @register decorator runs.
from . import checklist            # 1  # noqa: F401,E402
from . import email_drafter        # 2  # noqa: F401,E402
from . import faq_chatbot          # 3  # noqa: F401,E402
from . import interview_summarizer # 4  # noqa: F401,E402
from . import sla_escalation       # 5  # noqa: F401,E402
from . import kt_reviewer          # 6  # noqa: F401,E402
from . import smart_routing        # 7  # noqa: F401,E402
from . import risk_assessment      # 8  # noqa: F401,E402
from . import compliance           # 9  # noqa: F401,E402
from . import dashboard_insights   # 10 # noqa: F401,E402

__all__ = ["REGISTRY", "AgentMeta", "BaseAgent", "Field"]
