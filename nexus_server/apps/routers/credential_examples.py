"""Standard OpenAI client examples shared by Router credential issuers."""
import json
import shlex


def system_one_credential_examples(*, base_url, models):
    """Native HTTP examples use the same Router key without pretending to be Chat."""
    model = models[0] if models else "<SYSTEM_ONE_ROUTER_MODEL>"
    payload = {"model": model, "state": "A customer needs a copy of an invoice.", "questions": {
        "team": {"type": "choice", "instructions": "Choose the team for this request.",
                 "criteria": {"billing": "Invoices and payments", "support": "Technical problems"}}}}
    return {
        "endpoint": base_url + "/systemone", "available_models": models, "recommended_model": model,
        "curl": 'curl "$OPENAI_BASE_URL/systemone" \\\n  -H "Authorization: Bearer $OPENAI_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -H "Idempotency-Key: $(python -c \'import uuid; print(uuid.uuid4())\')" \\\n  -d ' + shlex.quote(json.dumps(payload)),
        "python": "\n".join([
            "import json, os, uuid", "from urllib.request import Request, urlopen", "",
            "# Set OPENAI_BASE_URL and OPENAI_API_KEY from Environment.",
            "payload = " + json.dumps(payload, indent=4), "",
            'request = Request(os.environ["OPENAI_BASE_URL"].rstrip("/") + "/systemone",',
            '    data=json.dumps(payload).encode(), headers={',
            '        "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],',
            '        "Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())})',
            "with urlopen(request, timeout=65) as response:",
            '    print(json.load(response)["answers"])', "",
        ]),
    }


def router_credential_examples(*, base_url, api_key, model, invoke_url):
    payload = json.dumps({"model": model, "messages": [{"role": "user", "content": "hello"}]})

    def curl(url):
        return "curl " + shlex.quote(url) + " \\\n  -H " + shlex.quote("Authorization: Bearer " + api_key) + \
            " \\\n  -H 'Content-Type: application/json' \\\n  -d " + shlex.quote(payload)

    return {
        "env": f"OPENAI_BASE_URL={base_url}\nOPENAI_API_KEY={api_key}\n",
        "curl": curl(base_url + "/chat/completions"),
        "invoke_curl": curl(invoke_url),
        "python": "\n".join([
            "from openai import OpenAI", "", "# Uses OPENAI_BASE_URL and OPENAI_API_KEY from your environment.",
            "client = OpenAI()", "", "response = client.chat.completions.create(",
            f"    model={json.dumps(model)},", '    messages=[{"role": "user", "content": "hello"}],',
            ")", "print(response.choices[0].message.content)", "",
        ]),
    }
