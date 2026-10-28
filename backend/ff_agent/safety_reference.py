"""Static, read-only safety reference catalogue."""

from copy import deepcopy

__all__ = ["reference_snapshot"]

_REFERENCE = {
    "schema_version": "1",
    "reference_only": True,
    "items": [
        {
            "code": "LOAN_ADVANCE_FEE",
            "title": "贷款前收费线索",
            "why": "先交保证金、验资费或激活费值得独立核实；单一线索不能判定当前事项。",
            "checks": [
                "通过贷款机构官网或已知官方客服电话核实放款主体和收费依据。",
                "不先转保证金、验资费或激活费，不提供密码或验证码，也不轻信追回承诺。",
            ],
            "reference_kind": "case_pattern",
            "cases": [
                {
                    "id": "C01",
                    "title": "下载非法放贷APP被骗4.5万元",
                    "publisher": "平川区人民政府／平川公安反诈中心",
                    "url": "https://www.bypc.gov.cn/zfxxgk/bmdwxzjd/xzjd/xpljdbsc/fdzdgknr/jwgk/dzsq/cwgk/art/2025/art_7c2bc9b394b14ec9b09b1af2b41c9e0d.html",
                    "archived_date": "2026-10-25",
                }
            ],
        },
        {
            "code": "REFUND_ADVANCE_PAYMENT",
            "title": "退款前付款线索",
            "why": "退款流程中要求先付款或提供敏感信息值得核实；退款一词本身不证明是诈骗。",
            "checks": [
                "从平台官方应用、官网或合同列明的入口核实退款流程。",
                "不为退款先付款，不提供密码或验证码，也不轻信退款或追回承诺。",
            ],
            "reference_kind": "rule_explanation",
            "cases": [],
        },
        {
            "code": "FAKE_CANCELLATION_PAYMENT",
            "title": "取消服务时要求付款的线索",
            "why": "以取消会员或自动扣费为由要求转账、共享屏幕或提供敏感信息值得核实；这不是单独的判定依据。",
            "checks": [
                "从平台官方应用或官网独立联系官方客服，核实会员和扣费情况。",
                "不按来电指引转账或共享屏幕，不提供密码或验证码，也不轻信追回承诺。",
            ],
            "reference_kind": "case_pattern",
            "cases": [
                {
                    "id": "C03",
                    "title": "取消平台会员险损失十九万",
                    "publisher": "广州市公安局花都分局",
                    "url": "https://www.huadu.gov.cn/zmhd/zsk/ga/content/post_10570403.html",
                    "archived_date": "2026-10-25"
                },
                {
                    "id": "C11",
                    "title": "平顶山何女士陷“关闭扣费”屏幕共享骗局被盗刷19800元",
                    "publisher": "平顶山市公安局",
                    "url": "https://gaj.pds.gov.cn/contents/18987/732773.html",
                    "archived_date": "2026-10-26"
                }
            ],
        },
        {
            "code": "REMOTE_ACCESS",
            "title": "远程访问或屏幕共享线索",
            "why": "引导安装远控软件或共享屏幕可能暴露账户信息；相似线索不能证明当前事项属于同一情形。",
            "checks": [
                "从相关机构官网或已知官方客服电话重新联系，核实对方身份和所述事项。",
                "不安装陌生远控软件或共享屏幕，不提供密码或验证码，也不轻信追回承诺。",
            ],
            "reference_kind": "case_pattern",
            "cases": [
                {
                    "id": "C02",
                    "title": "快递丢失理赔被骗二十多万",
                    "publisher": "广州市公安局花都分局",
                    "url": "https://www.huadu.gov.cn/zmhd/zsk/ga/content/post_10570403.html",
                    "archived_date": "2026-10-25"
                },
                {
                    "id": "C03",
                    "title": "取消平台会员险损失十九万",
                    "publisher": "广州市公安局花都分局",
                    "url": "https://www.huadu.gov.cn/zmhd/zsk/ga/content/post_10570403.html",
                    "archived_date": "2026-10-25"
                },
                {
                    "id": "C10",
                    "title": "工行济源分行劝阻冒充航司客服屏幕共享诈骗",
                    "publisher": "济源日报",
                    "url": "https://www.jiyuan.gov.cn/zwyw/bmdt/t995271.html",
                    "archived_date": "2026-10-26"
                },
                {
                    "id": "C11",
                    "title": "平顶山何女士陷“关闭扣费”屏幕共享骗局被盗刷19800元",
                    "publisher": "平顶山市公安局",
                    "url": "https://gaj.pds.gov.cn/contents/18987/732773.html",
                    "archived_date": "2026-10-26"
                }
            ],
        },
        {
            "code": "TASK_RECHARGE",
            "title": "任务垫资或充值线索",
            "why": "任务中要求持续垫资或充值值得核实；前期返利不能证明后续能够提现。",
            "checks": [
                "通过招聘平台或发布机构的官方入口核实任务主体、规则和提现条件。",
                "不向陌生平台垫资或充值，不提供密码或验证码，也不轻信追回承诺。",
            ],
            "reference_kind": "case_pattern",
            "cases": [
                {
                    "id": "C04",
                    "title": "商品退款理赔被诱导刷单损失五万",
                    "publisher": "广州市公安局花都分局",
                    "url": "https://www.huadu.gov.cn/zmhd/zsk/ga/content/post_10570403.html",
                    "archived_date": "2026-10-25",
                }
            ],
        },
        {
            "code": "SAFE_ACCOUNT",
            "title": "转入所谓安全账户的线索",
            "why": "要求向所谓安全账户转账值得独立核实；出现该说法本身不能判定当前事项。",
            "checks": [
                "通过银行官网、已知官方客服电话或网点核实账户及处理要求。",
                "不向个人或陌生账户转账，不提供密码或验证码，也不轻信追回承诺。",
            ],
            "reference_kind": "case_pattern",
            "cases": [
                {
                    "id": "C09",
                    "title": "武汉民警劝阻冒充金融客服屏幕共享诈骗 保住张先生23万元",
                    "publisher": "武汉市公安局",
                    "url": "https://gaj.wuhan.gov.cn/jmzx/jqfb/202607/t20260722_2823933.html",
                    "archived_date": "2026-10-26"
                }
            ],
        },
        {
            "code": "SECRET_DISCLOSURE",
            "title": "索要密码或验证码的线索",
            "why": "索要密码、短信验证码等敏感信息值得警惕；单一线索不能确认当前事项的性质。",
            "checks": [
                "使用已知的机构官网或官方应用入口独立核实来电、短信或消息。",
                "不透露密码或验证码，不按陌生指引共享屏幕，也不轻信退款或追回承诺。",
            ],
            "reference_kind": "case_pattern",
            "cases": [
                {
                    "id": "C05",
                    "title": "冒充抖音客服骗局被家人及时劝阻",
                    "publisher": "宁夏回族自治区通信管理局",
                    "url": "https://nxca.miit.gov.cn/ztzl/Appqhyhqyzxzz/art/2025/art_b2d6ceb967fc431fa98cdceba479cadd.html",
                    "archived_date": "2026-10-25"
                },
                {
                    "id": "C10",
                    "title": "工行济源分行劝阻冒充航司客服屏幕共享诈骗",
                    "publisher": "济源日报",
                    "url": "https://www.jiyuan.gov.cn/zwyw/bmdt/t995271.html",
                    "archived_date": "2026-10-26"
                },
                {
                    "id": "C11",
                    "title": "平顶山何女士陷“关闭扣费”屏幕共享骗局被盗刷19800元",
                    "publisher": "平顶山市公安局",
                    "url": "https://gaj.pds.gov.cn/contents/18987/732773.html",
                    "archived_date": "2026-10-26"
                }
            ],
        },
        {
            "code": "CREDIT_REPAIR_PAYMENT",
            "title": "付费修复征信的线索",
            "why": "以付费保证修复征信为由提出要求值得核实；该线索不能单独判定具体情形。",
            "checks": [
                "通过征信机构或金融机构的官方入口查询记录并核实异议处理流程。",
                "不支付所谓内部修复费，不提供密码或验证码，也不轻信修复或追回承诺。",
            ],
            "reference_kind": "rule_explanation",
            "cases": [],
        },
        {
            "code": "INVESTMENT_QUALIFICATION_PAYMENT",
            "title": "投资资格付费线索",
            "why": "以取得投资资格为由要求先付款值得核实；单一线索不能说明当前产品或请求的性质。",
            "checks": [
                "通过监管机构或交易机构的官方名录独立核实机构资质和产品信息。",
                "不向个人账户支付资格费，不提供密码或验证码，也不轻信收益或追回承诺。",
            ],
            "reference_kind": "rule_explanation",
            "cases": [],
        },
        {
            "code": "REGULATORY_ACCOUNT_TRANSFER",
            "title": "以监管名义要求转账的线索",
            "why": "以监管或调查名义要求转账值得独立核实；此线索本身不能证明当前事项属于诈骗。",
            "checks": [
                "通过监管机构官网或已知官方客服电话独立核实所述事项。",
                "不向个人账户转账，不提供密码或验证码，也不轻信退款或追回承诺。",
            ],
            "reference_kind": "rule_explanation",
            "cases": [],
        },
    ],
}


def reference_snapshot() -> dict:
    """Return an independent copy of the static reference catalogue."""
    return deepcopy(_REFERENCE)
