import assert from 'node:assert/strict'
import { test } from 'node:test'

// The loader supplies host/UI dependencies; these tests exercise exported
// presentation behavior without reaching a real account or a running desktop.
const plugin = await import('../desktop/plugin.js').catch(error => {
  if (error.code === 'ERR_MODULE_NOT_FOUND') return {}
  throw error
})

test('quota view keeps missing allowance unknown instead of displaying zero', () => {
  assert.equal(typeof plugin.quotaView, 'function', 'quotaView must exist')
  const view = plugin.quotaView({ available: false, reason: 'Sign in required' })
  assert.equal(view.label, 'Codex · unavailable')
  assert.equal(view.remaining, null)
  assert.equal(view.reason, 'Sign in required')
})

test('quota view preserves distinct windows and their reset timestamps', () => {
  assert.equal(typeof plugin.quotaView, 'function')
  const windows = [
    { label: 'Session', used_percent: 77, reset_at: '2026-09-19T12:30:12Z' },
    { label: 'Weekly', used_percent: 12, reset_at: null },
  ]
  const view = plugin.quotaView({ available: true, windows })
  assert.equal(view.label, 'Codex · 23% left')
  assert.deepEqual(view.windows.map(w => [w.label, w.remaining, w.reset_at]), [
    ['Session', 23, windows[0].reset_at], ['Weekly', 88, null],
  ])
  assert.equal(plugin.quotaView({ available: true, windows: windows.slice(0, 1) }).windows.length, 1)
})
