"""server_cluster.llm.data_definitions — typed payloads crossing
the host's module boundaries (repo convention: pydantic BaseModels):
the OpenAI chat-completions shapes (chat_api) and the host's management
payloads (admin: model status, segmentation, the dummy controls)."""
from .admin import (
    ControlRequest, ControlResponse, HealthResponse, LoadOffloadResult,
    ModelStatus, ModelStatusList, SegmentRequest, SegmentResponse,
    StatsResponse,
)
from .chat_api import (
    ChatChunk, ChatChoice, ChatCompletionEnvelope, ChatCompletionRequest,
    ChatMessage, ChoiceMessage, DeltaMessage, ImageContentNotSupported,
    StreamOptions, Usage, error_body, new_completion_id,
)
from .jev_api import (
    ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneQuestion,
    SystemOneRequest,
)

__all__ = [
    "ChatChunk", "ChatChoice", "ChatCompletionEnvelope",
    "ChatCompletionRequest", "ChatMessage", "ChoiceAnswer", "ChoiceMessage",
    "ControlRequest", "ControlResponse", "DeltaMessage",
    "HealthResponse", "ImageContentNotSupported", "LoadOffloadResult",
    "ModelStatus", "ModelStatusList", "NoulAnswer", "ScoreAnswer",
    "SegmentRequest", "SegmentResponse", "StatsResponse", "StreamOptions",
    "SystemOneQuestion", "SystemOneRequest", "Usage", "error_body",
    "new_completion_id",
]
