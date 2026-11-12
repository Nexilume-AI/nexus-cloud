from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from .openai_compat import OpenAIChatCompletionsView, apply_api_key_context
from .system_one_protocol import NativeJSONParser, validate_request
from .system_one_services import invoke


class SystemOneView(OpenAIChatCompletionsView):
    parser_classes = [NativeJSONParser]
    renderer_classes = [JSONRenderer]

    def post(self, request):
        apply_api_key_context(request)
        result = invoke(request=request, payload=validate_request(request.data))
        return Response(result, headers={"Cache-Control": "no-store", "X-Request-ID": request.request_id})
