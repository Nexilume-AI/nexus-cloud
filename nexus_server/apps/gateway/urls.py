from __future__ import annotations

from django.urls import path

from .claude_compat import ClaudeMessagesView, ClaudeModelsView
from .openai_compat import OpenAIChatCompletionsView, OpenAIModelsView, OpenAIResponsesView
from .views import ChatCompletionsView
from .image_views import OpenAIImagesView


urlpatterns = [
    *[path(f"openai/v1/images/{endpoint}{slash}", OpenAIImagesView.as_view(operation=operation))
      for endpoint, operation in [("generations", "images.generate"), ("edits", "images.edit"), ("variations", "images.variation")]
      for slash in ("", "/")],
    path("chat/completions/", ChatCompletionsView.as_view(), name="gateway-chat-completions"),
    path("openai/v1/chat/completions", OpenAIChatCompletionsView.as_view(), name="openai-compatible-chat-completions"),
    path("openai/v1/chat/completions/", OpenAIChatCompletionsView.as_view(), name="openai-compatible-chat-completions-slash"),
    path("openai/v1/responses", OpenAIResponsesView.as_view(), name="openai-compatible-responses"),
    path("openai/v1/responses/", OpenAIResponsesView.as_view(), name="openai-compatible-responses-slash"),
    path("openai/v1/models", OpenAIModelsView.as_view(), name="openai-compatible-models"),
    path("openai/v1/models/", OpenAIModelsView.as_view(), name="openai-compatible-models-slash"),
    path("claude/v1/messages", ClaudeMessagesView.as_view(), name="claude-compatible-messages"),
    path("claude/v1/messages/", ClaudeMessagesView.as_view(), name="claude-compatible-messages-slash"),
    path("claude/v1/models", ClaudeModelsView.as_view(), name="claude-compatible-models"),
    path("claude/v1/models/", ClaudeModelsView.as_view(), name="claude-compatible-models-slash"),
]
