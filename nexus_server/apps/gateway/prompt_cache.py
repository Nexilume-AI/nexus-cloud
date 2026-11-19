"""OpenAI cache usage and configured tariffs; never manufacture a cache hit."""
from decimal import Decimal, ROUND_DOWN
from types import SimpleNamespace

WRITE_PRICES = (
    "cache_write_price_per_1k_tokens",
    "cache_write_30m_price_per_1k_tokens",
    "cache_write_12h_price_per_1k_tokens",
)


def cache_usage(usage, input_tokens):
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    details = details if isinstance(details, dict) else {}
    adjusted = False
    def count(value, maximum):
        nonlocal adjusted
        try:
            n = int(value)
            if isinstance(value, bool) or n != value:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            adjusted = True
            return 0
        bounded = min(max(n, 0), maximum)
        adjusted |= bounded != n
        return bounded
    total = max(int(input_tokens), 0)
    read = count(details.get("cached_tokens", 0), total)
    split = details.get("cache_write_tokens_details") or {}
    split = split if isinstance(split, dict) else {}
    short = count(split.get("ttl_30m_tokens", 0), total - read)
    long = count(split.get("ttl_12h_tokens", 0), total - read - short)
    write = count(details.get("cache_write_tokens", short + long), total - read)
    adjusted |= short + long > write
    short = min(short, write)
    long = min(long, write - short)
    return {"reported": any(k in details for k in ("cached_tokens", "cache_write_tokens", "cache_write_tokens_details")),
            "cached_tokens": read, "cache_write_tokens": write,
            "cache_write_tokens_details": {"ttl_30m_tokens": short, "ttl_12h_tokens": long},
            "uncached_input_tokens": total - read - write, "adjusted": adjusted}


def pricing_snapshot(tariff):
    legacy = Decimal(getattr(tariff, "price_per_1k_tokens", 0) or 0)
    def price(name, fallback):
        value = getattr(tariff, name, None)
        return Decimal(value) if value is not None else fallback
    input_price = price("input_price_per_1k_tokens", legacy)
    output_price = price("output_price_per_1k_tokens", legacy)
    write = price(WRITE_PRICES[0], input_price)
    return {"input_price_per_1k_tokens": input_price, "output_price_per_1k_tokens": output_price,
            "cached_input_price_per_1k_tokens": price("cached_input_price_per_1k_tokens", input_price),
            "reasoning_output_price_per_1k_tokens": price("reasoning_output_price_per_1k_tokens", output_price),
            WRITE_PRICES[0]: write, WRITE_PRICES[1]: price(WRITE_PRICES[1], write),
            WRITE_PRICES[2]: price(WRITE_PRICES[2], write)}


def token_charge(*, tariff, request_tokens, response_tokens, cached_input_tokens=0,
                 reasoning_output_tokens=0, cache=None):
    prices = pricing_snapshot(tariff)
    # Revalidate even an internal receipt; legacy callers may only supply reads.
    data = cache_usage({"input_tokens_details": cache or {"cached_tokens": cached_input_tokens}}, request_tokens)
    split = data["cache_write_tokens_details"]
    short, long = split["ttl_30m_tokens"], split["ttl_12h_tokens"]
    unspecified = data["cache_write_tokens"] - short - long
    reasoning = min(max(int(reasoning_output_tokens or 0), 0), max(int(response_tokens), 0))
    quantities = {"input_price_per_1k_tokens": data["uncached_input_tokens"],
                  "cached_input_price_per_1k_tokens": data["cached_tokens"],
                  WRITE_PRICES[0]: unspecified, WRITE_PRICES[1]: short, WRITE_PRICES[2]: long,
                  "output_price_per_1k_tokens": max(int(response_tokens), 0) - reasoning,
                  "reasoning_output_price_per_1k_tokens": reasoning}
    amount = sum((Decimal(n) * prices[k] for k, n in quantities.items()), Decimal(0)) / Decimal(1000)
    return amount.quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN), prices


def receipt(response, tariff=None):
    value = dict(response.cache_usage or cache_usage({}, response.request_tokens))
    if tariff is not None:
        raw, prices = token_charge(tariff=tariff, request_tokens=response.request_tokens,
            response_tokens=response.response_tokens, cached_input_tokens=response.cached_input_tokens,
            reasoning_output_tokens=response.reasoning_output_tokens, cache=response.cache_usage)
        value["configured_prices"] = {k: str(v) for k, v in prices.items()}
        value["configured_raw_amount"] = str(raw)
    return value


def configured_tariff(deployment):
    """Only explicit differentiated rates replace a legacy flat Source tariff."""
    offer = getattr(deployment, "runtime_model_offer", None)
    names = ("input_price_per_1k_tokens", "output_price_per_1k_tokens",
             "cached_input_price_per_1k_tokens", "reasoning_output_price_per_1k_tokens", *WRITE_PRICES)
    if offer is None or not any(getattr(offer, name, None) is not None for name in names):
        return None
    return SimpleNamespace(price_per_1k_tokens=deployment.pricing_rate,
                           **{name: getattr(offer, name, None) for name in names})
