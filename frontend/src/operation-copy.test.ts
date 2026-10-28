import { test } from 'node:test';
import assert from 'node:assert/strict';
import { operationCopy } from './operation-copy.ts';

test('定时授权不会被解释为已经转账',()=>{
 const c=operationCopy('scheduled_transfer_authorize');assert.equal(c.kind,'scheduled');assert.equal(c.title,'定时转账授权');assert.match(c.verificationReason,/核对身份/);assert.match(c.successLabel,/不是付款|另查付款/)
})

test('转账把累计金额门槛放在核验原因中', () => {
  const copy = operationCopy('transfer');
  assert.equal(copy.title, '转账');
  assert.equal(copy.kind, 'transfer');
  assert.equal(copy.verificationReason, '累计付款和待处理金额超过1,000元');
  assert.notEqual(copy.title, copy.verificationReason);
});

test('订阅查询说明查询范围，但不声称查询已完成', () => {
  const copy = operationCopy('subscription_query');
  assert.equal(copy.title, '查询订阅与代扣');
  assert.equal(copy.kind, 'subscription_query');
  assert.match(copy.successLabel, /订阅与代扣/);
  assert.doesNotMatch(copy.successLabel, /已完成|查询完成/);
});

test('取消自动续费不声称续费已经停止', () => {
  const copy = operationCopy('subscription_cancel');
  assert.equal(copy.title, '取消自动续费');
  assert.equal(copy.kind, 'subscription_cancel');
  assert.match(copy.successLabel, /核对取消结果/);
  assert.doesNotMatch(copy.successLabel, /续费已停止|已取消|取消成功/);
});

test('挂失卡片要求核对身份，并使用模拟完成文案', () => {
  const copy = operationCopy('card_report_lost');
  assert.equal(copy.title, '挂失卡片');
  assert.equal(copy.kind, 'card');
  assert.equal(copy.verificationReason, '卡片操作需要核对身份');
  assert.equal(copy.successLabel, '模拟挂失已完成');
  assert.doesNotMatch(copy.verificationReason, /1,000元/);
});

test('解除挂失要求核对身份，并使用对应的模拟完成文案', () => {
  const copy = operationCopy('card_restore');
  assert.equal(copy.title, '解除挂失');
  assert.equal(copy.kind, 'card');
  assert.equal(copy.verificationReason, '卡片操作需要核对身份');
  assert.equal(copy.successLabel, '模拟解挂已完成');
});

test('未知业务返回不支持提示，不伪称订阅业务已完成', () => {
  const copy = operationCopy('unrecognized_operation');
  assert.equal(copy.title, '暂不支持的业务');
  assert.equal(copy.kind, 'unsupported');
  assert.equal(copy.successLabel, '请核对原业务回执');
  assert.doesNotMatch(copy.successLabel, /订阅.*已完成|续费已停止/);
});

test('每次调用返回独立结果，不受先前结果修改影响', () => {
  const first = operationCopy('transfer');
  const second = operationCopy('transfer');
  assert.notStrictEqual(first, second);
  first.title = '已修改';
  assert.equal(second.title, '转账');
});

test('模拟申购赎回使用独立身份核对原因，不冒充转账或订阅',()=>{
 for(const [type,title] of [['wealth_purchase','模拟申购'],['wealth_redemption','模拟赎回']]){
  const copy=operationCopy(type);assert.equal(copy.title,title);assert.equal(copy.kind,'wealth');assert.match(copy.verificationReason,/理财/);assert.doesNotMatch(copy.verificationReason,/1,000/);assert.match(copy.successLabel,/核对/)
 }
})

test('卡片消费管控和演示口令有独立可确认文案',()=>{
 for(const [type,kind] of [['card_spend','card_spend'],['card_controls_update','card_control'],['card_credential_change','card_credential']]){
  const copy=operationCopy(type);assert.equal(copy.kind,kind);assert.match(copy.successLabel,/核对|模拟/);assert.notEqual(copy.kind,'unsupported')
 }
 assert.match(operationCopy('card_spend').title,/模拟.*消费/)
 assert.match(operationCopy('card_credential_change').title,/演示口令/)
})
