// Pure decoders and API wrapper for the demo investment profile.
// No UI, auth, storage, logging or retries; the injected request owns CSRF/base URL.
// Decoders fail closed: anything malformed throws instead of defaulting.
export const QUESTION_IDS = ['loss_capacity', 'liquidity', 'horizon', 'experience', 'income_stability', 'attitude'] as const
export type QuestionId = typeof QUESTION_IDS[number]
export type Answers = Record<QuestionId, number>
export type ProfileStatus = 'NONE' | 'VALID' | 'EXPIRED'
export interface Contribution { id: QuestionId; index: number; points: number }
export interface Profile {
  version: number; survey_version: string; answers: Answers; score: number; level: number
  contributions: Contribution[]; caps: unknown[]; created_at: number; expires_at: number
}
export interface Receipt { submitted_version: number }
export interface ProfileView { status: ProfileStatus; profile: Profile | null; simulated: true; replayed: boolean; receipt: Receipt | null }
export interface Choice { index: number; label: string; points: number }
export interface Question { id: QuestionId; text: string; choices: Choice[] }
export interface Questionnaire { survey_version: string; validity_days: number; simulated: true; disclaimer: string; questions: Question[] }
export interface AssessmentSummary { version: number; level: number; survey_version: string; expires_at: number }
export interface ComparisonAssessment { status: ProfileStatus; profile: AssessmentSummary | null; simulated: true }
export interface ComparedProduct {
  id: string; name: string; nav_cents: number; quote_version: number; risk_level: number; minimum_cents: number
  recommended_holding_days: number; eligible: boolean | null; reason: string; simulated: true
}
export interface Comparison {
  products: ComparedProduct[]; assessment: ComparisonAssessment; policy_version: string
  policy_fingerprint: string; simulated: true; disclaimer: string
}
export type RequestFn = <T>(path: string, body?: unknown) => Promise<T>
export interface InvestmentClient {
  load(): Promise<{ survey: Questionnaire; view: ProfileView; comparison: Comparison }>
  submit(answers: unknown, surveyVersion: string, expectedVersion: number, requestKey: string, confirmed: boolean): Promise<ProfileView>
}

type Obj = Record<string, unknown>
const fail = (what: string): never => { throw new Error('invalid investment data: ' + what) }
const obj = (v: unknown, n: string): Obj =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Obj : fail(n)
const arr = (v: unknown, n: string): unknown[] => Array.isArray(v) ? v : fail(n)
const int = (v: unknown, n: string, min: number, max = Number.MAX_SAFE_INTEGER): number =>
  Number.isSafeInteger(v) && (v as number) >= min && (v as number) <= max ? v as number : fail(n)
const str = (v: unknown, n: string): string => typeof v === 'string' && v.length > 0 ? v : fail(n)
const bool = (v: unknown, n: string): boolean => typeof v === 'boolean' ? v : fail(n)
const yes = (v: unknown, n: string): true => v === true ? true : fail(n)
const time = (v: unknown, n: string): number =>
  typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : fail(n)
const status = (v: unknown): ProfileStatus =>
  v === 'NONE' || v === 'VALID' || v === 'EXPIRED' ? v : fail('status')
const qid = (v: unknown): QuestionId =>
  (QUESTION_IDS as readonly unknown[]).includes(v) ? v as QuestionId : fail('question id')
const own = (o: Obj, k: string) => Object.prototype.hasOwnProperty.call(o, k)

function parseAnswers(raw: unknown): Answers {
  const o = obj(raw, 'answers')
  if (Object.keys(o).length !== QUESTION_IDS.length || !QUESTION_IDS.every(id => own(o, id))) fail('answers')
  return Object.fromEntries(QUESTION_IDS.map(id => [id, int(o[id], id, 0, 3)])) as Answers
}

function parseProfile(raw: unknown): Profile {
  const p = obj(raw, 'profile')
  const created_at = time(p.created_at, 'created_at')
  const expires_at = time(p.expires_at, 'expires_at')
  if (expires_at <= created_at) fail('expiry')
  const contributions = arr(p.contributions, 'contributions').map(c => {
    const o = obj(c, 'contribution')
    return { id: qid(o.id), index: int(o.index, 'index', 0, 3), points: int(o.points, 'points', 0, 3) }
  })
  return {
    version: int(p.version, 'version', 1), survey_version: str(p.survey_version, 'survey_version'),
    answers: parseAnswers(p.answers), score: int(p.score, 'score', 0, 18), level: int(p.level, 'level', 1, 5),
    contributions, caps: [...arr(p.caps, 'caps')], created_at, expires_at,
  }
}

export function parseProfileView(raw: unknown): ProfileView {
  const v = obj(raw, 'view')
  const s = status(v.status)
  const profile = s === 'NONE' ? (v.profile === null ? null : fail('profile')) : parseProfile(v.profile)
  const receipt = v.receipt === null ? null
    : { submitted_version: int(obj(v.receipt, 'receipt').submitted_version, 'submitted_version', 1) }
  const replayed = bool(v.replayed, 'replayed')
  if (replayed && receipt === null || !replayed && receipt !== null) fail('replay receipt')
  if (s === 'NONE' && receipt !== null) fail('receipt without profile')
  if (receipt && profile && receipt.submitted_version > profile.version) fail('receipt version')
  return { status: s, profile, simulated: yes(v.simulated, 'simulated'), replayed, receipt }
}

export function parseQuestionnaire(raw: unknown): Questionnaire {
  const q = obj(raw, 'questionnaire')
  const items = arr(q.questions, 'questions')
  if (items.length !== QUESTION_IDS.length) fail('question count')
  const questions = items.map((item, i) => {
    const o = obj(item, 'question')
    if (o.id !== QUESTION_IDS[i]) fail('question order')
    const choices = arr(o.choices, 'choices')
    if (choices.length !== 4) fail('choice count')
    // Rebuild objects so no server-side preselected value can leak into the form.
    return {
      id: qid(o.id), text: str(o.text, 'text'),
      choices: choices.map((c, index) => {
        const x = obj(c, 'choice')
        if (x.index !== index || x.points !== index) fail('choice index')
        return { index, label: str(x.label, 'label'), points: index }
      }),
    }
  })
  return {
    survey_version: str(q.survey_version, 'survey_version'), validity_days: int(q.validity_days, 'validity_days', 1),
    simulated: yes(q.simulated, 'simulated'), disclaimer: str(q.disclaimer, 'disclaimer'), questions,
  }
}

function parseAssessment(raw: unknown): ComparisonAssessment {
  const a = obj(raw, 'assessment')
  const s = status(a.status)
  let profile: AssessmentSummary | null = null
  if (s !== 'NONE') {
    const p = obj(a.profile, 'assessment profile')
    profile = { version: int(p.version, 'version', 1), level: int(p.level, 'level', 1, 5),
      survey_version: str(p.survey_version, 'survey_version'), expires_at: time(p.expires_at, 'expires_at') }
  } else if (a.profile !== null) fail('assessment profile')
  return { status: s, profile, simulated: yes(a.simulated, 'simulated') }
}

export function parseComparison(raw: unknown): Comparison {
  const c = obj(raw, 'comparison')
  const assessment = parseAssessment(c.assessment)
  const list = arr(c.products, 'products')
  if (list.length < 1 || list.length > 2) fail('product count')
  const products = list.map(item => {
    const p = obj(item, 'product')
    const risk_level = int(p.risk_level, 'risk_level', 1, 5)
    // Eligibility is display-only; it must follow the minimal assessment, never invented.
    const expected = assessment.status === 'VALID' && assessment.profile ? assessment.profile.level >= risk_level : null
    if (p.eligible !== expected) fail('eligible')
    return {
      id: str(p.id, 'id'), name: str(p.name, 'name'), nav_cents: int(p.nav_cents, 'nav_cents', 1),
      quote_version: int(p.quote_version, 'quote_version', 1), risk_level,
      minimum_cents: int(p.minimum_cents, 'minimum_cents', 1),
      recommended_holding_days: int(p.recommended_holding_days, 'recommended_holding_days', 1),
      eligible: expected, reason: str(p.reason, 'reason'), simulated: yes(p.simulated, 'simulated'),
    }
  })
  if (new Set(products.map(p => p.id)).size !== products.length) fail('duplicate product')
  const fingerprint = str(c.policy_fingerprint, 'policy_fingerprint')
  if (!/^[0-9a-f]{64}$/.test(fingerprint)) fail('policy_fingerprint')
  return {
    products, assessment, policy_version: str(c.policy_version, 'policy_version'), policy_fingerprint: fingerprint,
    simulated: yes(c.simulated, 'simulated'), disclaimer: str(c.disclaimer, 'disclaimer'),
  }
}

function sameAssessment(view: ProfileView, a: ComparisonAssessment): void {
  if (view.status !== a.status) fail('mixed assessment status')
  const p = view.profile, s = a.profile
  if ((p === null) !== (s === null)) fail('mixed assessment')
  if (p && s && (p.version !== s.version || p.level !== s.level ||
    p.survey_version !== s.survey_version || p.expires_at !== s.expires_at)) fail('mixed assessment version')
}

export function createInvestmentClient(request: RequestFn): InvestmentClient {
  return {
    async load() {
      // Three reads, no body, no retry; inconsistent snapshots are rejected, not merged.
      const [s, v, c] = await Promise.all([
        request<unknown>('/investment-profile/questionnaire'),
        request<unknown>('/investment-profile'),
        request<unknown>('/wealth/compare'),
      ])
      const survey = parseQuestionnaire(s), view = parseProfileView(v), comparison = parseComparison(c)
      sameAssessment(view, comparison.assessment)
      return { survey, view, comparison }
    },
    async submit(answers, surveyVersion, expectedVersion, requestKey, confirmed) {
      // Validate everything before HTTP; the caller owns the key and decides any retry.
      if (confirmed !== true) fail('confirmation required')
      const body = {
        answers: parseAnswers(answers), survey_version: str(surveyVersion, 'survey_version'),
        expected_version: int(expectedVersion, 'expected_version', 0),
        request_key: str(requestKey, 'request_key'), confirmed: true,
      }
      return parseProfileView(await request<unknown>('/investment-profile', body))
    },
  }
}
