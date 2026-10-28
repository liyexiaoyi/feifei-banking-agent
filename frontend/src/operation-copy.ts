export type OperationCopy = {
  title: string;
  verificationReason: string;
  successLabel: string;
  kind: 'transfer' | 'subscription_query' | 'subscription_cancel' | 'card' | 'card_spend' | 'card_control' | 'card_credential' | 'virtual_card' | 'wealth' | 'scheduled' | 'birthday_budget' | 'birthday_order' | 'unsupported';
};

export function operationCopy(type: string): OperationCopy {
  switch (type) {
    case 'transfer':
      return {
        title: '转账',
        verificationReason: '累计付款和待处理金额超过1,000元',
        successLabel: '请核对转账回执',
        kind: 'transfer',
      };
    case 'subscription_query':
      return {
        title: '查询订阅与代扣',
        verificationReason: '',
        successLabel: '请查看订阅与代扣信息',
        kind: 'subscription_query',
      };
    case 'subscription_cancel':
      return {
        title: '取消自动续费',
        verificationReason: '',
        successLabel: '请核对取消结果',
        kind: 'subscription_cancel',
      };
    case 'wealth_purchase':
      return {title:'模拟申购',verificationReason:'模拟理财交易需要核对身份',successLabel:'请核对模拟申购回执',kind:'wealth'};
    case 'wealth_redemption':
      return {title:'模拟赎回',verificationReason:'模拟理财交易需要核对身份',successLabel:'请核对模拟赎回回执',kind:'wealth'};
    case 'virtual_card_create':
      return {title:'申请虚拟卡',verificationReason:'',successLabel:'请核对虚拟卡申请结果',kind:'virtual_card'};
    case 'card_report_lost':
      return {
        title: '挂失卡片',
        verificationReason: '卡片操作需要核对身份',
        successLabel: '模拟挂失已完成',
        kind: 'card',
      };
    case 'card_restore':
      return {
        title: '解除挂失',
        verificationReason: '卡片操作需要核对身份',
        successLabel: '模拟解挂已完成',
        kind: 'card',
      };
    case 'scheduled_transfer_authorize':
      return {title:'定时转账授权',verificationReason:'定时授权需要核对身份',successLabel:'授权已记录，请另查付款结果',kind:'scheduled'};
    case 'card_spend':
      return {title:'模拟卡片消费',verificationReason:'累计付款和待处理金额超过1,000元',successLabel:'请核对模拟消费回执',kind:'card_spend'};
    case 'card_controls_update':
      return {title:'调整交易设置',verificationReason:'卡片操作需要核对身份',successLabel:'请核对交易设置结果',kind:'card_control'};
    case 'card_credential_change':
      return {title:'设置演示口令',verificationReason:'卡片操作需要核对身份',successLabel:'请核对演示口令设置结果',kind:'card_credential'};
    case 'birthday_budget_reserve':
      return {title:'预算预留',verificationReason:'预算预留需要核对身份',successLabel:'请核对预算预留回执',kind:'birthday_budget'};
    case 'birthday_order':
      return {title:'生日模拟付款',verificationReason:'模拟付款需要核对身份',successLabel:'请核对模拟付款回执',kind:'birthday_order'};
    default:
      return {
        title: '暂不支持的业务',
        verificationReason: '',
        successLabel: '请核对原业务回执',
        kind: 'unsupported',
      };
  }
}
