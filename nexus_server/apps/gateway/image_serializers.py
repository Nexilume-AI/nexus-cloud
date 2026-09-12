from rest_framework import serializers


class ImagePricingRowSerializer(serializers.Serializer):
    operation = serializers.ChoiceField(choices=["images.generate", "images.edit", "images.variation"])
    size = serializers.RegexField(r"^(auto|[1-9][0-9]{1,4}x[1-9][0-9]{1,4})$", max_length=32)
    quality = serializers.ChoiceField(choices=["auto", "standard", "hd", "low", "medium", "high"])
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=0, coerce_to_string=True)
    currency = serializers.ChoiceField(choices=["USD"], default="USD")


class ModelContractSerializer(serializers.Serializer):
    operations = serializers.ListField(child=serializers.ChoiceField(choices=["chat.completions", "responses", "images.generate", "images.edit", "images.variation"]), allow_empty=False)
    input_modalities = serializers.ListField(child=serializers.ChoiceField(choices=["text", "image"]), allow_empty=False)
    output_modalities = serializers.ListField(child=serializers.ChoiceField(choices=["text", "image"]), allow_empty=False)

    def validate(self, attrs):
        if any(op.startswith("images.") for op in attrs["operations"]) and "image" not in attrs["output_modalities"]:
            raise serializers.ValidationError("Image operations require image output.")
        if any(op in attrs["operations"] for op in ("images.edit", "images.variation")) and "image" not in attrs["input_modalities"]:
            raise serializers.ValidationError("Image edits and variations require image input.")
        return attrs


class ImageRequestSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields) - {"image", "image[]", "mask"}
        if unknown:
            raise serializers.ValidationError({field: "Unsupported image parameter." for field in sorted(unknown)})
        return super().to_internal_value(data)

    model = serializers.CharField(max_length=255)
    router_id = serializers.UUIDField(required=False)
    prompt = serializers.CharField(max_length=32000, required=False)
    n = serializers.IntegerField(min_value=1, max_value=4, default=1)
    size = serializers.RegexField(r"^(auto|[1-9][0-9]{1,4}x[1-9][0-9]{1,4})$", max_length=32, default="auto")
    quality = serializers.ChoiceField(choices=["auto", "standard", "hd", "low", "medium", "high"], default="auto")
    response_format = serializers.ChoiceField(choices=["url", "b64_json"], default="url")
    output_format = serializers.ChoiceField(choices=["png", "jpeg", "webp"], required=False)
    background = serializers.ChoiceField(choices=["auto", "opaque", "transparent"], required=False)

    def validate(self, attrs):
        operation = self.context["operation"]
        if operation != "images.variation" and not attrs.get("prompt"):
            raise serializers.ValidationError({"prompt": "A prompt is required."})
        return attrs
