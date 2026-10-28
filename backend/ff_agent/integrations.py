"""Local capability disclosure only; no provider is connected by this module."""
from copy import deepcopy


class RealModeRejected(Exception):
    """Raised when a real integration mode is requested."""


class InvalidIntegrationMode(ValueError):
    """Raised when the requested integration mode is invalid."""


_SNAPSHOT = {
    'schema_version': '1',
    'mode': 'simulated',
    'capabilities': [
        {
            'id': 'bank',
            'status': 'simulated',
            'real_mode': 'rejected',
            'note_code': 'mock_ledger',
        },
        {
            'id': 'identity',
            'status': 'simulated',
            'real_mode': 'rejected',
            'note_code': 'mock_verification',
        },
        {
            'id': 'human',
            'status': 'not_connected',
            'real_mode': 'rejected',
            'note_code': 'awaiting_official_docs',
        },
    ],
}


def get_snapshot(mode='simulated'):
    if not isinstance(mode, str):
        raise InvalidIntegrationMode('Invalid integration mode')
    if mode == 'real':
        raise RealModeRejected('Real integration mode is not available')
    if mode != 'simulated':
        raise InvalidIntegrationMode('Invalid integration mode')
    return deepcopy(_SNAPSHOT)
