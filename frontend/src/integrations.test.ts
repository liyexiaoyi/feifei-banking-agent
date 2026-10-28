import test from 'node:test';
import assert from 'node:assert/strict';
import { parseIntegrations, loadIntegrationStatus } from './integrations.ts';

const snapshot = () => ({
  schema_version: '1', mode: 'simulated', capabilities: [
    { id: 'bank', status: 'simulated', real_mode: 'rejected', note_code: 'mock_ledger' },
    { id: 'identity', status: 'simulated', real_mode: 'rejected', note_code: 'mock_verification' },
    { id: 'human', status: 'not_connected', real_mode: 'rejected', note_code: 'awaiting_official_docs' }
  ]
});

test('accepts shuffled entries, normalizes order, and copies data', () => {
  const source = snapshot();
  source.capabilities.reverse();
  const result = parseIntegrations(source);
  assert.deepEqual(result, snapshot());
  source.capabilities[0].note_code = 'changed';
  assert.deepEqual(result, snapshot());
});

test('rejects missing, duplicate, and extra capabilities', () => {
  const missing = snapshot();
  missing.capabilities.pop();
  assert.equal(parseIntegrations(missing), null);
  const duplicate = snapshot();
  duplicate.capabilities[2] = { ...duplicate.capabilities[0] };
  assert.equal(parseIntegrations(duplicate), null);
  const extra = snapshot();
  extra.capabilities.push({ id: 'other', status: 'simulated', real_mode: 'rejected', note_code: 'mock_ledger' });
  assert.equal(parseIntegrations(extra), null);
});

test('rejects unknown schema, mode, status, and invalid note combinations', () => {
  for (const change of [
    { schema_version: '2' }, { mode: 'real' },
    { capabilities: [{ ...snapshot().capabilities[0], status: 'connected' }, ...snapshot().capabilities.slice(1)] },
    { capabilities: [{ ...snapshot().capabilities[0], note_code: 'mock_verification' }, ...snapshot().capabilities.slice(1)] }
  ]) assert.equal(parseIntegrations({ ...snapshot(), ...change }), null);
});

test('load uses one read-only path and failure clears a prior success', async () => {
  const paths: string[] = [];
  const good = await loadIntegrationStatus(async <T>(path: string): Promise<T> => {
    paths.push(path);
    return snapshot() as T;
  });
  assert.deepEqual(good, snapshot());
  const failed = await loadIntegrationStatus(async <T>(path: string): Promise<T> => {
    paths.push(path);
    throw new Error('unauthorized');
  });
  assert.equal(failed, null);
  assert.deepEqual(paths, ['/integrations', '/integrations']);
  assert.equal(await loadIntegrationStatus(async <T>(): Promise<T> => ({ bad: true }) as T), null);
});
