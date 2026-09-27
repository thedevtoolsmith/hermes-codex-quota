import assert from 'node:assert/strict'
import { test } from 'node:test'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { JSDOM } from 'jsdom'
import { QueryClient, QueryClientProvider, host } from '@hermes/plugin-sdk'
import * as pluginModule from '../desktop/plugin.js'
const plugin = pluginModule.default

function environment() {
  const dom = new JSDOM('<!doctype html><html><body><div id="app"></div></body></html>', { url: 'http://localhost' })
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  return dom
}

test('status chip loads quota and opens details; Refresh makes a new read', async () => {
  const dom = environment()
  assert.equal(typeof plugin?.register, 'function', 'desktop plugin must register a status-bar component')
  const contributions = []
  const calls = []
  const snapshot = { available: true, session_id: 'runtime-1', profile: 'default', provider: 'openai-codex',
    plan: 'Test plan', fetched_at: new Date().toISOString(),
    windows: [{ label: 'Session', used_percent: 77, reset_at: '2026-09-19T12:30:12Z' }], details: [] }
  plugin.register({ register: c => contributions.push(c), rest: async path => { calls.push(path); return snapshot } })
  const chip = contributions.find(c => c.area === 'statusBar.right')
  assert.ok(chip)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const root = createRoot(document.querySelector('#app'))
  try {
    await act(async () => {
      root.render(React.createElement(QueryClientProvider, { client }, chip.render()))
      await new Promise(resolve => setTimeout(resolve, 40))
    })
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)) })
    assert.match(document.body.textContent, /Codex · 23% left/)
    assert.match(calls[0], /session_id=runtime-1/)
    assert.match(calls[0], /expected_profile=default/)
    await act(async () => document.querySelector('[data-codex-quota="chip"]').click())
    assert.match(document.body.textContent, /77% used/)
    assert.match(document.body.textContent, /Last refreshed/)
    const before = calls.length
    await act(async () => {
      document.querySelector('[data-codex-quota="refresh"]').click()
      await new Promise(resolve => setTimeout(resolve, 40))
    })
    assert.ok(calls.length > before)
    await act(async () => {
      host.state.focusedSessionId.set('runtime-2')
      await new Promise(resolve => setTimeout(resolve, 40))
    })
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)) })
    assert.doesNotMatch(document.querySelector('[data-codex-quota="chip"]').textContent, /23%/,
      'a response for another session must not render as the newly focused session quota')
  } finally {
    await act(async () => root.unmount())
    client.clear()
    dom.window.close()
    host.state.focusedSessionId.set('runtime-1')
  }
})

test('refresh failures keep the previous reading explicitly stale and hide raw errors', async () => {
  const dom = environment()
  const contributions = []
  let fail = false
  plugin.register({ register: c => contributions.push(c), rest: async () => {
    if (fail) throw new Error('Authorization Bearer PRIVATE_SENTINEL')
    return { available: true, session_id: 'runtime-1', profile: 'default', fetched_at: new Date().toISOString(),
      windows: [{ label: 'Session', used_percent: 77 }], details: [] }
  } })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const root = createRoot(document.querySelector('#app'))
  try {
    await act(async () => {
      root.render(React.createElement(QueryClientProvider, { client }, contributions[0].render()))
      await new Promise(resolve => setTimeout(resolve, 40))
    })
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)) })
    await act(async () => document.querySelector('[data-codex-quota="chip"]').click())
    fail = true
    await act(async () => {
      document.querySelector('[data-codex-quota="refresh"]').click()
      await new Promise(resolve => setTimeout(resolve, 40))
    })
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)) })
    assert.match(document.querySelector('[data-codex-quota="chip"]').textContent, /23% left · stale/)
    assert.doesNotMatch(document.body.textContent, /PRIVATE_SENTINEL/)
    await act(async () => host.state.focusedSessionProfile.set('other'))
    assert.doesNotMatch(document.querySelector('[data-codex-quota="chip"]').textContent, /23%/)
    assert.equal(document.querySelector('[data-codex-quota="refresh"]').disabled, true)
  } finally {
    await act(async () => root.unmount())
    client.clear()
    dom.window.close()
    host.state.focusedSessionProfile.set('default')
  }
})
