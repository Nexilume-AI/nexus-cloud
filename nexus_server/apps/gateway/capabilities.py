"""Explicit model operation and modality contracts."""
from rest_framework.exceptions import APIException

OPERATIONS = ("chat.completions", "responses", "images.generate", "images.edit", "images.variation")


class ModelOperationUnsupported(APIException):
    status_code = 400
    default_code = "MODEL_OPERATION_UNSUPPORTED"
    default_detail = "No available Source supports the requested operation and input modalities."


class OperationPolicyDenied(APIException):
    status_code = 403
    default_code = "OPERATION_POLICY_DENIED"
    default_detail = "This API key is not authorized for the requested model operation."


def model_contract(model):
    operations = list(getattr(model, "operations", []) or [])
    # Preserve historical chat/vision compatibility, never infer image generation.
    return {
        "operations": operations or ["chat.completions", "responses"],
        "input_modalities": list(getattr(model, "input_modalities", []) or []) or (["text", "image"] if not operations else ["text"]),
        "output_modalities": list(getattr(model, "output_modalities", []) or []) or ["text"],
        "declared": bool(operations),
    }


def deployment_contract(deployment):
    contract = model_contract(deployment.canonical_model)
    offer = getattr(deployment, "runtime_model_offer", None)
    declared = (getattr(offer, "metadata", {}) or {}).get("model_contract")
    if isinstance(declared, dict) and isinstance(declared.get("operations"), list):
        contract = {key: list(declared.get(key, [])) for key in ("operations", "input_modalities", "output_modalities")}
        contract["declared"] = True
    return contract


def request_operation(payload):
    return payload.get("_nexus_operation", "chat.completions")


def group_contract(*, tenant, group):
    from .services import model_group_deployment_candidates
    sources = model_group_deployment_candidates(tenant=tenant, group=group) if group is not None else []
    contracts = [deployment_contract(source) for source in sources]
    return {
        field: sorted({value for contract in contracts for value in contract[field]})
        for field in ("operations", "input_modalities", "output_modalities")
    }


def supports_request(deployment, payload):
    contract = deployment_contract(deployment)
    operation = request_operation(payload)
    image_input = operation in ("images.edit", "images.variation") or any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for message in payload.get("messages", [])
        for part in (message.get("content", []) if isinstance(message.get("content"), list) else [])
    )
    return (
        operation in contract["operations"]
        and (not image_input or "image" in contract["input_modalities"])
        and (not operation.startswith("images.") or "image" in contract["output_modalities"])
    )


def filter_candidates(deployments, payload, *, required=True):
    candidates = [item for item in deployments if supports_request(item, payload)]
    if request_operation(payload).startswith("images.") and candidates:
        from rest_framework.exceptions import ValidationError
        from .image_services import image_price
        priced = []
        last_error = None
        for item in candidates:
            try:
                # Unsaved, request-local value: cost policies compare USD/image,
                # never a text-token rate for an image operation.
                item.pricing_rate = image_price(item, request_operation(payload), payload)
                priced.append(item)
            except ValidationError as exc:
                last_error = exc
        if not priced and required and last_error is not None:
            raise last_error
        candidates = priced
    if deployments and not candidates and required:
        raise ModelOperationUnsupported()
    return candidates


def enforce_operation_policy(api_key, operation):
    if api_key is None:
        return
    policy = getattr(api_key, "policy", None)
    allowed = getattr(policy, "allowed_operations", []) or ["chat.completions", "responses"]
    if operation not in allowed:
        raise OperationPolicyDenied()
