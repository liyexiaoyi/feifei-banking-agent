"""Read-only synthetic product comparison against the user's explicit demo assessment.

No financial writes, no real market data, no suitability or return promises.
Comparison creates no application; purchases are rechecked server-side against the
current profile and the demo policy fingerprint defined here (single source).
"""
import hashlib
import json

from .bank import DomainError
from .investment_profile import ProfileService

POLICY_VERSION = 'demo-products-v1'
DEFAULT_IDS = ('demo_cash', 'demo_bond')
# Fixed, clearly synthetic metadata. Holding days are illustrative only, not a redemption lock.
METADATA = {
    'demo_cash': {'risk_level': 1, 'minimum_cents': 100, 'recommended_holding_days': 1},
    'demo_bond': {'risk_level': 3, 'minimum_cents': 10000, 'recommended_holding_days': 90},
}
DISCLAIMER = ('全部为演示数据：风险等级、起购金额与建议持有天数均为模拟设定，'
               '不构成银行适当性评估、投资建议或收益承诺；比较本身不创建申请，'
               '申购时服务端会重新核验当前演示测评与演示产品策略。')
NEED_ASSESSMENT = '先完成/更新演示测评'


def _invalid(message):
    return DomainError('VALIDATION_ERROR', message, 422)


def _normalize(product_ids):
    if product_ids is None:
        return list(DEFAULT_IDS)
    if type(product_ids) is not list or not 1 <= len(product_ids) <= 2:
        raise _invalid('请选择1至2个演示产品进行比较。')
    for pid in product_ids:
        if type(pid) is not str or pid not in METADATA:
            raise _invalid('包含未知的演示产品。')
    if len(set(product_ids)) != len(product_ids):
        raise _invalid('比较产品不能重复。')
    return list(product_ids)


PROFILE_FIELDS = ('version', 'level', 'survey_version', 'expires_at')


def policy_fingerprint(product_id=None):
    '''SHA-256 of canonical demo policy; all products by default, else one known product.'''
    if product_id is None:
        metadata = METADATA
    elif type(product_id) is str and product_id in METADATA:
        metadata = {product_id: METADATA[product_id]}
    else:
        raise _invalid('包含未知的演示产品。')
    canonical = json.dumps({'policy_version': POLICY_VERSION, 'metadata': metadata},
                           sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _minimal(view):
    '''Project the private profile view to four fields; never answers/contributions.'''
    if not isinstance(view, dict):
        return {'status': 'NONE', 'profile': None, 'simulated': True}
    profile = view.get('profile')
    summary = {key: profile.get(key) for key in PROFILE_FIELDS} if isinstance(profile, dict) else None
    return {'status': view.get('status'), 'profile': summary, 'simulated': True}


def _eligibility(assessment, risk_level):
    if not isinstance(assessment, dict):
        return None, NEED_ASSESSMENT
    profile = assessment.get('profile')
    if assessment.get('status') != 'VALID' or not isinstance(profile, dict):
        return None, NEED_ASSESSMENT
    level = profile.get('level')
    if type(level) is not int:
        return None, NEED_ASSESSMENT
    if level >= risk_level:
        return True, f'演示测评等级{level}不低于产品演示风险等级{risk_level}。'
    return False, f'演示测评等级{level}低于产品演示风险等级{risk_level}。'


class WealthComparison:
    def __init__(self, bank):
        self.bank = bank

    def compare(self, user, product_ids=None):
        ids = _normalize(product_ids)
        with self.bank.transaction() as db:
            # Shares this db; ProfileService checks user existence and never infers answers.
            assessment = _minimal(ProfileService(self.bank).current(db, user))
            products = []
            for pid in ids:
                row = db.execute(
                    'SELECT id,name,nav_cents,quote_version FROM wealth_products WHERE id=?',
                    (pid,)).fetchone()
                if row is None:
                    raise _invalid('包含未知的演示产品。')
                meta = METADATA[pid]
                eligible, reason = _eligibility(assessment, meta['risk_level'])
                products.append({
                    'id': row[0], 'name': row[1], 'nav_cents': row[2], 'quote_version': row[3],
                    'risk_level': meta['risk_level'], 'minimum_cents': meta['minimum_cents'],
                    'recommended_holding_days': meta['recommended_holding_days'],
                    'eligible': eligible, 'reason': reason, 'simulated': True,
                })
        return {
            'products': products, 'assessment': assessment,
            'policy_version': POLICY_VERSION, 'policy_fingerprint': policy_fingerprint(),
            'simulated': True, 'disclaimer': DISCLAIMER,
        }
