import test from 'node:test';
import assert from 'node:assert/strict';
import {
  loadComponent,
  elements,
  textContent,
} from './component-test-loader.mjs';
const {default:IntegrationStatusCard}=await loadComponent('IntegrationStatusCard.tsx');

const VALID_SNAPSHOT = {
  schema_version: '1',
  mode: 'simulated',
  capabilities: [
    { id: 'bank', status: 'simulated', real_mode: 'rejected', note_code: 'mock_ledger' },
    { id: 'identity', status: 'simulated', real_mode: 'rejected', note_code: 'mock_verification' },
    { id: 'human', status: 'not_connected', real_mode: 'rejected', note_code: 'awaiting_official_docs' },
  ],
};

test('loading 时三格均显示核对中，且不出现成功文案', () => {
  const view = IntegrationStatusCard({
    snapshot: VALID_SNAPSHOT,
    loading: true,
  });
  const text = textContent(view);
  assert.match(text, /核对中/);
  assert.doesNotMatch(text, /已接入/);
  assert.doesNotMatch(text, /状态未知/);
});

test('snapshot 为 null 时显示状态未知并提示核对官方渠道', () => {
  const view = IntegrationStatusCard({
    snapshot: null,
    loading: false,
  });
  const text = textContent(view);
  assert.match(text, /状态未知/);
  assert.match(text, /官方渠道/);
  assert.doesNotMatch(text, /已接入/);
  assert.doesNotMatch(text, /核对中/);
});

test('合法 snapshot 渲染三个固定标题，真人客服显示未接入', () => {
  const view = IntegrationStatusCard({
    snapshot: VALID_SNAPSHOT,
    loading: false,
  });
  const text = textContent(view);
  assert.match(text, /模拟银行/);
  assert.match(text, /模拟验证/);
  assert.match(text, /真人客服/);
  assert.match(text, /未接入/);
  assert.match(text, /模拟演示中/);
  assert.doesNotMatch(text, /已接入/);
});


test('卡片不含任何按钮或链接等可执行控件', () => {
  const cases = [
    { snapshot: VALID_SNAPSHOT, loading: false },
    { snapshot: null, loading: false },
    { snapshot: VALID_SNAPSHOT, loading: true },
  ];
  for (const props of cases) {
    const view = IntegrationStatusCard(props);
    assert.equal(elements(view, 'button').length, 0);
    assert.equal(elements(view, 'a').length, 0);
    assert.equal(elements(view, 'input').length, 0);
    assert.equal(elements(view, 'form').length, 0);
  }
});
