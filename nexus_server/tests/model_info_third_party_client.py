"""Run with inspect-ai==0.3.287 and litellm==1.108.0; secrets arrive on stdin.

Tests the installed clients, not copied parsers. Optional inference uses only
the caller-supplied model and a bounded one-line prompt.
"""
import asyncio
import importlib.metadata
import json
import sys

import httpx
from inspect_ai.model import get_model, get_model_info, GenerateConfig
from inspect_ai.model._providers._litellm_proxy_model_info import proxy_deployments
from litellm.types.router import Deployment


async def main():
    config = json.load(sys.stdin)
    base, key, model = config["base_url"], config["api_key"], config["model"]
    headers = config.get("headers", {})
    response = httpx.get(base + "/model/info", headers={"Authorization": "Bearer " + key, **headers}, timeout=30)
    assert response.status_code == 200, response.status_code
    rows = response.json()["data"]
    for row in rows:
        Deployment(**row)
    deployments = proxy_deployments(base, key, headers)
    assert any(d.model_name == model for d in deployments)
    api = get_model("litellm-proxy/" + model, base_url=base, api_key=key,
                    default_headers=headers, config=GenerateConfig(max_tokens=4096, max_retries=0))
    info = get_model_info("litellm-proxy/" + model)
    assert info is not None and info.input_tokens
    result = {"inspect_ai": importlib.metadata.version("inspect-ai"),
              "litellm": importlib.metadata.version("litellm"),
              "validated_rows": len(rows), "input_tokens": info.input_tokens,
              "output_tokens": info.output_tokens, "client_constructed": True}
    if config.get("generate"):
        output = await api.generate("Reply exactly MODEL_INFO_OK, without explanation.")
        result["completion_ok"] = output.completion.strip() == "MODEL_INFO_OK"
        result["stop_reason"] = output.stop_reason
        assert result["completion_ok"], "Unexpected completion"
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
