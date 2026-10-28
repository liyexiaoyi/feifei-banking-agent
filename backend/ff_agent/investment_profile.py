'''Synthetic demo risk assessment (B4 step 1). Personal profile data only; never moves money.'''
import hashlib
import json
import re

from .bank import DomainError

SURVEY_VERSION = 'demo-risk-v1'
VALIDITY_DAYS = 180
VALIDITY_SECONDS = VALIDITY_DAYS * 86400
KEY_RE = re.compile(r'[A-Za-z0-9_-]{8,64}')
DISCLAIMER = '本问卷为队伍自定义的模拟演示规则，不是银行官方适当性评定，不构成投资建议，不承诺保本或收益。'
QUESTIONS = (
    ('loss_capacity', '如果投入的钱出现亏损，您能承受多少？',
     ('不能承受任何亏损', '可承受小幅亏损', '可承受中等亏损', '可承受较大亏损')),
    ('liquidity', '这笔钱近期需要随时取用吗？',
     ('随时可能要用', '半年内可能要用', '一年内不太会用', '三年内都不用')),
    ('horizon', '您计划持有多长时间？',
     ('不到1个月', '1到6个月', '6个月到2年', '2年以上')),
    ('experience', '您有过哪类投资经历？',
     ('从未投资', '只存过定期或货币基金', '买过债券或混合基金', '买过股票或股票基金')),
    ('income_stability', '您的收入稳定吗？',
     ('没有稳定收入', '收入波动较大', '收入基本稳定', '收入稳定且有结余')),
    ('attitude', '面对价格下跌，您更可能怎么做？',
     ('马上全部卖出', '卖出一部分', '继续持有观察', '考虑适当加仓')),
)
IDS = tuple(q[0] for q in QUESTIONS)
# Choosing index 0 on these questions caps the demo level, with a human-readable reason.
CAPS = {
    'loss_capacity': (1, '不能承受亏损，演示级别上限为1级'),
    'liquidity': (1, '资金随时可能要用，演示级别上限为1级'),
    'horizon': (2, '计划持有不到1个月，演示级别上限为2级'),
}
PROFILE_COLUMNS = 'version,survey_version,answers,score,level,contributions,caps,created_at,expires_at'


def bootstrap_profiles(bank):
    with bank.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS investment_profiles(user_id TEXT NOT NULL,'
                   'version INTEGER NOT NULL CHECK(version>=1),survey_version TEXT NOT NULL,'
                   'answers TEXT NOT NULL,score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 18),'
                   'level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 5),contributions TEXT NOT NULL,'
                   'caps TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,'
                   'PRIMARY KEY(user_id,version))')
        db.execute('CREATE TABLE IF NOT EXISTS investment_profile_requests(user_id TEXT NOT NULL,'
                   'request_key TEXT NOT NULL,request_hash TEXT NOT NULL,version INTEGER NOT NULL,'
                   'PRIMARY KEY(user_id,request_key))')
        db.execute('CREATE TRIGGER IF NOT EXISTS investment_profiles_immutable BEFORE UPDATE ON '
                   'investment_profiles BEGIN SELECT RAISE(ABORT, ' + repr('profile immutable') + '); END')


def _invalid(code, message):
    return DomainError(code, message, 422)


def _validate(answers, survey_version, expected_version, request_key, confirmed):
    if confirmed is not True:
        raise _invalid('CONSENT_REQUIRED', '需要本人明确确认后提交')
    if type(survey_version) is not str or survey_version != SURVEY_VERSION:
        raise _invalid('INVALID_SURVEY', '问卷版本不受支持')
    if (type(answers) is not dict or set(answers) != set(IDS)
            or any(type(v) is not int or not 0 <= v <= 3 for v in answers.values())):
        raise _invalid('INVALID_ANSWERS', '请逐题从0到3中选择，6题都需要回答')
    if type(expected_version) is not int or expected_version < 0:
        raise _invalid('INVALID_VERSION', '版本号无效')
    if type(request_key) is not str or not request_key.isascii() or not KEY_RE.fullmatch(request_key):
        raise _invalid('INVALID_REQUEST_KEY', '请求键无效')


def _hash(answers, survey_version, expected_version):
    raw = json.dumps({'answers': answers, 'survey_version': survey_version,
                      'expected_version': expected_version}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _evaluate(answers):
    contributions = [{'id': qid, 'index': answers[qid], 'points': answers[qid]} for qid in IDS]
    score = sum(answers[qid] for qid in IDS)
    raw_level = 1 + sum(score >= t for t in (4, 8, 12, 16))
    caps = [{'id': qid, 'max_level': limit, 'reason': reason}
            for qid, (limit, reason) in CAPS.items() if answers[qid] == 0]
    level = min([raw_level] + [c['max_level'] for c in caps])
    return score, level, contributions, caps


def _view(row, now):
    if row is None:
        return {'status': 'NONE', 'profile': None, 'simulated': True, 'replayed': False, 'receipt': None}
    version, survey, answers, score, level, contributions, caps, created, expires = tuple(row)
    profile = {'version': version, 'survey_version': survey, 'answers': json.loads(answers),
               'score': score, 'level': level, 'contributions': json.loads(contributions),
               'caps': json.loads(caps), 'created_at': created, 'expires_at': expires}
    return {'status': 'EXPIRED' if now >= expires or survey != SURVEY_VERSION else 'VALID', 'profile': profile,
            'simulated': True, 'replayed': False, 'receipt': None}


class ProfileService:
    def __init__(self, bank):
        self.bank = bank

    def questionnaire(self):
        return {'survey_version': SURVEY_VERSION, 'validity_days': VALIDITY_DAYS, 'simulated': True,
                'disclaimer': DISCLAIMER,
                'questions': [{'id': qid, 'text': text,
                               'choices': [{'index': i, 'label': label, 'points': i}
                                           for i, label in enumerate(labels)]}
                              for qid, text, labels in QUESTIONS]}

    def get(self, user):
        with self.bank.transaction() as db:
            return self.current(db, user)

    def current(self, db, user):
        '''Read-only view inside the caller transaction; never refreshes expiry.'''
        self.bank._user(db, user)
        row = db.execute('SELECT ' + PROFILE_COLUMNS + ' FROM investment_profiles WHERE user_id=? '
                         'ORDER BY version DESC LIMIT 1', (user,)).fetchone()
        return _view(row, self.bank.now().timestamp())

    def require(self, db, user, version=None):
        view = self.current(db, user)
        if view['status'] == 'NONE':
            raise DomainError('ASSESSMENT_REQUIRED', '请先完成演示风险测评', 409)
        if view['status'] == 'EXPIRED':
            raise DomainError('ASSESSMENT_EXPIRED', '演示风险测评已过期或问卷已更新，请重新测评', 409)
        if version is not None and (type(version) is not int or version != view['profile']['version']):
            raise DomainError('PROFILE_CHANGED', '演示风险测评已更新，请重新确认', 409)
        return view['profile']

    def binding_snapshot(self, db, user, version):
        if type(version) is not int or version < 1:
            raise DomainError('INVALID_VERSION', '请明确核对当前演示测评版本。', 422)
        profile = self.require(db, user, version)
        return {key: profile[key] for key in ('version', 'level', 'survey_version', 'expires_at')}

    def submit(self, user, answers, survey_version, expected_version, request_key, confirmed=False):
        _validate(answers, survey_version, expected_version, request_key, confirmed)
        answers = {qid: answers[qid] for qid in IDS}
        digest = _hash(answers, survey_version, expected_version)
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            prior = db.execute('SELECT request_hash,version FROM investment_profile_requests '
                               'WHERE user_id=? AND request_key=?', (user, request_key)).fetchone()
            if prior is not None:
                if prior[0] != digest:
                    raise DomainError('IDEMPOTENCY_MISMATCH', '同一请求键对应了不同内容', 409)
                view = self.current(db, user)
                view.update(replayed=True, receipt={'submitted_version': prior[1]})
                return view
            latest = db.execute('SELECT COALESCE(MAX(version),0) FROM investment_profiles WHERE user_id=?',
                                (user,)).fetchone()[0]
            if latest != expected_version:
                raise DomainError('VERSION_CONFLICT', '测评已被更新，请刷新后重试', 409)
            score, level, contributions, caps = _evaluate(answers)
            created = int(self.bank.now().timestamp())
            version = latest + 1
            db.execute('INSERT INTO investment_profiles(user_id,' + PROFILE_COLUMNS + ') '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (user, version, survey_version, json.dumps(answers), score, level,
                        json.dumps(contributions, ensure_ascii=False), json.dumps(caps, ensure_ascii=False),
                        created, created + VALIDITY_SECONDS))
            db.execute('INSERT INTO investment_profile_requests VALUES(?,?,?,?)',
                       (user, request_key, digest, version))
            self.bank._event(db, user, None, 'INVESTMENT_PROFILE_SUBMITTED', json.dumps({
                'version': version, 'level': level, 'survey_version': survey_version}, sort_keys=True))
            return self.current(db, user)
