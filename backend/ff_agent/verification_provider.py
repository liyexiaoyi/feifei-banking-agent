'''I2 offline-only simulated verification provider. No network, no IO, no third-party libraries.'''
import hmac
from dataclasses import dataclass
from enum import Enum


class VerifyResult(str, Enum):
    VERIFIED = 'VERIFIED'
    REJECTED = 'REJECTED'
    UNKNOWN = 'UNKNOWN'
    REVOKED = 'REVOKED'


@dataclass(frozen=True)
class Challenge:
    challenge_id: str
    user: str
    opid: str
    purpose: str
    action_hash: str
    expires_at: float


class LocalDemoProvider:
    '''Trusted local demo verifier: constant-time comparison with the simulated code only.'''
    offline_only = True
    simulated = True
    _DEMO_CODE = b'246810'

    def verify(self, challenge, code):
        if not isinstance(challenge, Challenge):
            return VerifyResult.REJECTED
        ok = hmac.compare_digest(str(code).encode('utf-8'), self._DEMO_CODE)
        return VerifyResult.VERIFIED if ok else VerifyResult.REJECTED
