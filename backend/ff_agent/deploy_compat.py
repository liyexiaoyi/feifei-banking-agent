"""Pure deployment compatibility policy.

Static metadata has four fields; only the runtime deployment requires ready.
"""
from .build_identity import valid_meta as validate_meta, IDS


def decide_deployment(expected: dict, probe_state: str, health: object = None) -> str:
    if not validate_meta(expected):
        return 'REJECT'
    if probe_state == 'absent':
        return 'START_ALLOWED'
    if probe_state != 'response' or not isinstance(health, dict):
        return 'REJECT'
    if health.get('service') != 'ff-agent' or health.get('status') != 'ok':
        return 'REJECT'
    actual = health.get('deployment')
    if not validate_meta(actual) or actual.get('build_state') != 'ready':
        return 'REJECT'
    return 'REUSE' if all(actual[k] == expected[k] for k in IDS) else 'REJECT'
