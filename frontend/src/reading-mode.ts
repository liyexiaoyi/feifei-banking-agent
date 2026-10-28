export type ReadingMode = 'normal' | 'comfortable'

export const READING_MODE_STORAGE_KEY = 'ff.reading-mode'

type StorageAccess = {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}
type StorageProvider = () => StorageAccess | undefined

const browserStorage: StorageProvider = () =>
  typeof window === 'undefined' ? undefined : window.localStorage

export function readReadingMode(storage: StorageProvider = browserStorage): ReadingMode {
  try {
    const value = storage()?.getItem(READING_MODE_STORAGE_KEY)
    return value === 'comfortable' ? 'comfortable' : 'normal'
  } catch {
    return 'normal'
  }
}

export function writeReadingMode(mode: ReadingMode, storage: StorageProvider = browserStorage): boolean {
  if (mode !== 'normal' && mode !== 'comfortable') return false
  try {
    const target = storage()
    if (!target) return false
    target.setItem(READING_MODE_STORAGE_KEY, mode)
    return true
  } catch {
    return false
  }
}
