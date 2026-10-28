export type IntegrationSnapshot = {
  schema_version: '1';
  mode: 'simulated';
  capabilities: [
    { id: 'bank'; status: 'simulated'; real_mode: 'rejected'; note_code: 'mock_ledger' },
    { id: 'identity'; status: 'simulated'; real_mode: 'rejected'; note_code: 'mock_verification' },
    { id: 'human'; status: 'not_connected'; real_mode: 'rejected'; note_code: 'awaiting_official_docs' }
  ];
};

const expected = [
  { id: 'bank', status: 'simulated', real_mode: 'rejected', note_code: 'mock_ledger' },
  { id: 'identity', status: 'simulated', real_mode: 'rejected', note_code: 'mock_verification' },
  { id: 'human', status: 'not_connected', real_mode: 'rejected', note_code: 'awaiting_official_docs' }
] as const;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function parseIntegrations(value: unknown): IntegrationSnapshot | null {
  try {
    if (!record(value) || value.schema_version !== '1' || value.mode !== 'simulated' ||
        !Array.isArray(value.capabilities) || value.capabilities.length !== expected.length) return null;
    const entries = value.capabilities;
    const ordered = expected.map((item) => {
      const matches = entries.filter((entry: unknown) => record(entry) && entry.id === item.id);
      if (matches.length !== 1) return null;
      const entry = matches[0] as Record<string, unknown>;
      if (entry.status !== item.status || entry.real_mode !== item.real_mode ||
          entry.note_code !== item.note_code) return null;
      return { ...item };
    });
    if (ordered.some((item) => item === null)) return null;
    return { schema_version: '1', mode: 'simulated', capabilities: ordered as IntegrationSnapshot['capabilities'] };
  } catch {
    return null;
  }
}

export async function loadIntegrationStatus(
  read: <T>(path: string) => Promise<T>
): Promise<IntegrationSnapshot | null> {
  try {
    return parseIntegrations(await read<unknown>('/integrations'));
  } catch {
    return null;
  }
}
