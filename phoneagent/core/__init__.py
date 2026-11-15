"""Algorithm layer: app maps, macro replay, and the task agent."""

from .agent import (
    AgentConfig,
    PhoneAgent,
    Route,
    Step,
    TaskResult,
    parse_action,
    parse_route,
)
from .app_map import (
    AppMap,
    Operation,
    Screen,
    ScreenElement,
    detect_xml_platform,
    texts_from_android_xml,
    texts_from_ios_xml,
)
from .llm import LLMClient, LLMError, MockLLM, OpenAICompatClient
from .macro import (
    MacroError,
    MacroExecutor,
    MacroResult,
    StepResult,
    validate_macro,
)

__all__ = [
    # app map
    "AppMap",
    "Operation",
    "Screen",
    "ScreenElement",
    "detect_xml_platform",
    "texts_from_android_xml",
    "texts_from_ios_xml",
    # macro
    "MacroError",
    "MacroExecutor",
    "MacroResult",
    "StepResult",
    "validate_macro",
    # agent
    "AgentConfig",
    "PhoneAgent",
    "Route",
    "Step",
    "TaskResult",
    "parse_action",
    "parse_route",
    # llm
    "LLMClient",
    "LLMError",
    "MockLLM",
    "OpenAICompatClient",
]
