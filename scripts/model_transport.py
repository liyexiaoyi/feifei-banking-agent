"""HTTP request envelopes and public-response extraction; no tools or I/O.

Protocol selection is explicit. A missing public reply is never reconstructed
from thinking or tool blocks. Used by the model runtime HTTP transport.
"""


def build_request(base, model, key, effort, budget, system, inputs, protocol):
    base = base.rstrip('/')
    headers = {'Authorization': 'Bearer ' + key}
    if protocol == 'openai':
        return base + '/chat/completions', headers, {
            'model': model, 'reasoning_effort': effort,
            'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': inputs}],
            'response_format': {'type': 'json_object'},
            'max_completion_tokens': budget,
        }
    if protocol == 'anthropic':
        headers['anthropic-version'] = '2026-10-21'
        return base.removesuffix('/v1') + '/v1/messages', headers, {
            'model': model, 'max_tokens': budget, 'system': system,
            'messages': [{'role': 'user', 'content': inputs}],
            'output_config': {'effort': effort},
        }
    raise ValueError('Unsupported model protocol')


def public_reply(body, protocol):
    if not isinstance(body, dict):
        raise ValueError('Invalid model response envelope')
    common = {key: body[key] for key in ('id', 'model', 'usage') if key in body}
    if protocol == 'openai':
        choice = body['choices'][0]
        message = choice.get('message', {})
        raw = message.get('content')
        public_message = {key: message[key] for key in ('role', 'content') if key in message}
        if 'reasoning_content' in message:
            public_message['reasoning_content_omitted'] = True
        finish = choice.get('finish_reason')
        archive = {**common, 'choices': [{'finish_reason': finish, 'message': public_message}]}
        return raw, archive, {'finish_reason': finish}
    if protocol == 'anthropic':
        blocks = body.get('content', [])
        if not isinstance(blocks, list):
            raise ValueError('Invalid Messages content blocks')
        text = [{'type': 'text', 'text': block['text']} for block in blocks
                if isinstance(block, dict) and block.get('type') == 'text'
                and isinstance(block.get('text'), str)]
        omitted = len(blocks) - len(text)
        finish = body.get('stop_reason')
        archive = {**common, 'content': text, 'stop_reason': finish,
                   'nontext_blocks_omitted': omitted}
        raw = '\n'.join(block['text'] for block in text) if text else None
        return raw, archive, {'finish_reason': finish, 'nontext_blocks_omitted': omitted}
    raise ValueError('Unsupported model protocol')
