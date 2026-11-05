from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework import serializers
from apps.deployments.models import Deployment
from apps.gateway.model_capabilities import validate_override
from apps.gateway.model_info import source_fields
from apps.gateway.model_metadata import endpoint_hash
from apps.providers.models import ProviderRuntimeAccount, ProviderRuntimeModelOffer


class CapabilityValidationTests(SimpleTestCase):
    def test_community_responses_preserves_explicit_effort_without_inventing_defaults(self):
        from apps.gateway.openai_compat import responses_payload_to_chat_payload
        from apps.gateway.serializers import ResponseCreateRequestSerializer, ChatCompletionRequestSerializer
        for stream in [False, True]:
            for effort in [None, "none", "ultra"]:
                with self.subTest(stream=stream, effort=effort):
                    body = {"model": "fixture", "input": "test", "stream": stream}
                    if effort is not None:
                        body["reasoning"] = {"effort": effort}
                    serializer = ResponseCreateRequestSerializer(data=body)
                    self.assertTrue(serializer.is_valid(), serializer.errors)
                    converted = responses_payload_to_chat_payload(serializer.validated_data)
                    chat = ChatCompletionRequestSerializer(data=converted)
                    self.assertTrue(chat.is_valid(), chat.errors)
                    self.assertEqual(chat.validated_data.get("reasoning_effort"), effort)
                    self.assertEqual("reasoning_effort" in converted, effort is not None)
                    self.assertEqual(chat.validated_data["stream"], stream)

    def test_typed_overrides_and_future_levels(self):
        self.assertIsNone(validate_override({"supports_reasoning": None}))
        self.assertEqual(validate_override({"supports_reasoning": False})["supports_reasoning"], False)
        self.assertEqual(validate_override({"reasoning_effort_levels": ["ultra", "none", "ultra"]})["reasoning_effort_levels"], ["none", "ultra"])
        for data in [{"supports_reasoning": "true"}, {"max_output_tokens": True}, {"context_window": -1},
                     {"reasoning_effort_levels": ["bad\nlevel"]}, {"api_key": "fixture"},
                     {"reasoning_effort_levels": ["low"], "default_reasoning_effort": "high"},
                     {"supports_reasoning": False, "reasoning_effort_levels": ["low"]},
                     {"supported_openai_params": ["messages"], "default_reasoning_effort": "low"}]:
            with self.subTest(data=data), self.assertRaises(serializers.ValidationError):
                validate_override(data)

    def test_request_efforts_are_bounded_and_not_guessed(self):
        from apps.gateway.serializers import ChatCompletionRequestSerializer, ResponseCreateRequestSerializer
        for value in ["none", "max", "ultra", "vendor-custom"]:
            body = ChatCompletionRequestSerializer(data={"model": "x", "messages": [{"role": "user", "content": "x"}], "reasoning_effort": value})
            self.assertTrue(body.is_valid(), body.errors)
            self.assertEqual(body.validated_data["reasoning_effort"], value)
        for value in [True, 12, {}, "", "a" * 65, "high\n"]:
            body = ChatCompletionRequestSerializer(data={"model": "x", "messages": [{"role": "user", "content": "x"}], "reasoning_effort": value})
            self.assertFalse(body.is_valid())
            body = ResponseCreateRequestSerializer(data={"model": "x", "input": "x", "reasoning": {"effort": value}})
            self.assertFalse(body.is_valid())
