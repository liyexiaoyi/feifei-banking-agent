"""Scrub developer output before JSON encoding; does not authenticate providers."""
import os
import re

SK_PATTERN = re.compile(r'sk-[A-Za-z0-9_-]{20,}', re.I)


def redact_public(value, secrets=None):
    if secrets is None:
        secrets = [v for k, v in os.environ.items()
                   if k.startswith('FF_') and k.endswith('_API_KEY') and v]
    tokens = sorted({v for v in secrets if isinstance(v, str) and v}, key=len, reverse=True)

    def text(value):
        for token in tokens:
            value = value.replace(token, '[REDACTED]')
        return SK_PATTERN.sub('[REDACTED]', value)

    def walk(item):
        if isinstance(item, str):
            return text(item)
        if isinstance(item, dict):
            return {text(k) if isinstance(k, str) else k: walk(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(v) for v in item]
        return item

    return walk(value)
