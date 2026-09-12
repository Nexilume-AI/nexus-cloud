"""Shared text/vision chat transport; other Gateway surfaces compose separately."""
from django.urls import path
from .views import ChatCompletionsView
from .openai_compat import OpenAIChatCompletionsView, OpenAIModelsView, OpenAIResponsesView
from .claude_compat import ClaudeMessagesView, ClaudeModelsView


urlpatterns = [
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
