from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.response import Response
from .openai_compat import OpenAIChatCompletionsView, apply_api_key_context
from .image_serializers import ImageRequestSerializer
from .image_services import images


class OpenAIImagesView(OpenAIChatCompletionsView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    operation = "images.generate"

    def post(self, request):
        apply_api_key_context(request)
        serializer = ImageRequestSerializer(data=request.data, context={"operation": self.operation})
        serializer.is_valid(raise_exception=True)
        return Response(images(request=request, payload=serializer.validated_data, operation=self.operation))
