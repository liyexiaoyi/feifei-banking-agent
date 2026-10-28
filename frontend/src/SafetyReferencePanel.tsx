import type { SafetyReferenceSnapshot } from './safety-reference';
import './safety-reference.css';

type Props = {
  codes: string[];
  reference: SafetyReferenceSnapshot | null;
  loading: boolean;
  error?: boolean;
};

const KNOWN_CODES = new Set([
  'LOAN_ADVANCE_FEE',
  'REFUND_ADVANCE_PAYMENT',
  'FAKE_CANCELLATION_PAYMENT',
  'REMOTE_ACCESS',
  'TASK_RECHARGE',
  'SAFE_ACCOUNT',
  'SECRET_DISCLOSURE',
  'CREDIT_REPAIR_PAYMENT',
  'INVESTMENT_QUALIFICATION_PAYMENT',
  'REGULATORY_ACCOUNT_TRANSFER',
]);

// 仅允许逐项核准的来源主机；其余来源须核对后加入。
const ACCEPTED_HOSTS = new Set([
  'www.bypc.gov.cn',
  'www.huadu.gov.cn',
  'nxca.miit.gov.cn',
  'gaj.wuhan.gov.cn',
  'www.jiyuan.gov.cn',
  'gaj.pds.gov.cn',
]);

function safeHref(value: string): string | null {
  try {
    const url = new URL(value);
    if (
      url.protocol !== 'https:' ||
      url.username !== '' ||
      url.password !== '' ||
      url.port !== '' ||
      !ACCEPTED_HOSTS.has(url.hostname)
    ) {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
}

export default function SafetyReferencePanel({
  codes,
  reference,
  loading,
  error = false,
}: Props) {
  const uniqueCodes = [...new Set(codes)];
  if (uniqueCodes.length === 0) return null;

  const availableReference =
    !loading &&
    !error &&
    reference?.schema_version === '1' &&
    reference.reference_only === true
      ? reference
      : null;

  return (
    <section className="safety-reference" aria-label="风险线索与参考说明">
      <h2 className="safety-reference__heading">风险线索与参考说明</h2>
      <p className="safety-reference__notice">
        本资料仅供独立核实参考。相似公开模式不证明当前请求就是诈骗；
        参考资料不能解除暂停或构成业务授权，也不提供损失追回服务。
      </p>

      {loading ? (
        <p className="safety-reference__status" role="status" aria-live="polite">
          正在加载参考资料，请稍候。
        </p>
      ) : !availableReference ? (
        <p className="safety-reference__status" role="status" aria-live="polite">
          参考资料暂不可用，无法展示相关线索与核实方法。既有暂停状态不受影响。
        </p>
      ) : (
        <div className="safety-reference__cards">
          {uniqueCodes.map((code) => {
            const matches = KNOWN_CODES.has(code)
              ? availableReference.items.filter((item) => item.code === code)
              : [];
            const item = matches.length === 1 ? matches[0] : null;

            if (!item) {
              return (
                <article className="safety-reference__card" key={code}>
                  <h3 className="safety-reference__title">风险参考</h3>
                  <details className="safety-reference__details">
                    <summary className="safety-reference__summary">
                      查看风险线索与核实方法
                    </summary>
                    <p className="safety-reference__unavailable">
                      此风险线索的参考资料暂不可用，无法提供已核对的说明或案例。
                    </p>
                  </details>
                </article>
              );
            }

            const hasCases =
              item.reference_kind === 'case_pattern' && item.cases.length > 0;

            return (
              <article className="safety-reference__card" key={code}>
                <h3 className="safety-reference__title">{item.title}</h3>
                <details className="safety-reference__details">
                  <summary className="safety-reference__summary">
                    查看风险线索与核实方法
                  </summary>
                  <div className="safety-reference__content">
                    <span className="safety-reference__badge">
                      {hasCases ? '公开模式参考' : '规则解释'}
                    </span>
                    <h4 className="safety-reference__subheading">相关线索</h4>
                    <p className="safety-reference__text">{item.why}</p>
                    <h4 className="safety-reference__subheading">独立核实方法</h4>
                    {item.checks.length > 0 ? (
                      <ul className="safety-reference__checks">
                        {item.checks.map((check, index) => (
                          <li key={index}>{check}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="safety-reference__unavailable">
                        具体核实方法暂不可用。
                      </p>
                    )}

                    <h4 className="safety-reference__subheading">相关案例 · 归档参考</h4>
                    {hasCases ? (
                      <ul className="safety-reference__cases">
                        {item.cases.map((entry, index) => {
                          const href = safeHref(entry.url);
                          return (
                            <li className="safety-reference__case" key={`${entry.id}:${index}`}>
                              {href ? (
                                <a
                                  className="safety-reference__link"
                                  href={href}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {entry.title}
                                  <span className="safety-reference__link-hint">（新窗口打开）</span>
                                </a>
                              ) : (
                                <span className="safety-reference__case-title">{entry.title}</span>
                              )}
                              <dl className="safety-reference__metadata">
                                <div>
                                  <dt>发布机构</dt>
                                  <dd>{entry.publisher}</dd>
                                </div>
                                <div>
                                  <dt>归档日期</dt>
                                  <dd>{entry.archived_date}</dd>
                                </div>
                              </dl>
                              {!href && (
                                <p className="safety-reference__unavailable">
                                  此案例链接暂不可用，来源地址未通过展示校验。
                                </p>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      <p className="safety-reference__text">
                        本项仅作规则解释，未提供直接支持本项线索的已核对公开案例。
                      </p>
                    )}
                    <p className="safety-reference__footnote">
                      归档参考不保证链接现时可用。案例中的相似模式仅供参考，不构成对当前请求的诈骗判定。
                    </p>
                  </div>
                </details>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
