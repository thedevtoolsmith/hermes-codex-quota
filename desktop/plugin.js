import { host, useValue, useQuery, Popover, PopoverTrigger, PopoverContent, Button, STATUSBAR_AREAS } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

export function quotaView(data) {
  const windows = data?.available ? (data.windows || []).map(window => ({
    ...window,
    remaining: typeof window.used_percent === 'number' && Number.isFinite(window.used_percent)
      ? Math.max(0, Math.min(100, Math.round(100 - window.used_percent))) : null,
  })) : []
  const remaining = windows[0]?.remaining ?? null
  return {
    label: remaining === null ? 'Codex · unavailable' : `Codex · ${remaining}% left`,
    remaining,
    windows,
    reason: data?.reason || (remaining === null ? 'No quota data available' : null),
  }
}

const missingAtom = { get: () => null, subscribe: () => () => {} }
const muted = { color: 'var(--ui-text-tertiary)' }

function timestamp(value) {
  const date = value ? new Date(value) : null
  return date && Number.isFinite(date.getTime()) ? date.toLocaleString() : 'Not reported'
}

function QuotaStatus({ ctx }) {
  const sessionId = useValue(host.state.focusedSessionId || missingAtom)
  const profile = useValue(host.state.focusedSessionProfile || missingAtom)
  const owner = useValue(host.state.focusedSessionOwner || missingAtom)
  const connection = useValue(host.state.connectionId || missingAtom)
  const activeProfile = useValue(host.state.profile)
  const model = useValue(host.state.model)
  const gateway = useValue(host.state.gateway)
  const scoped = !!sessionId && !!owner && connection === 'local'
    && owner.connectionId === connection && owner.profile === profile && activeProfile === profile
  const query = useQuery({
    queryKey: ['codex-quota', connection, profile, sessionId, model, gateway],
    enabled: scoped && gateway === 'open',
    queryFn: async () => {
      // ctx.rest follows the active gateway, not arbitrary tiles. Recheck the
      // captured owner immediately before the call, and verify the reply too.
      const currentOwner = host.state.focusedSessionOwner?.get()
      if (host.state.focusedSessionId.get() !== sessionId
        || host.state.connectionId?.get() !== connection
        || host.state.profile.get() !== profile
        || currentOwner?.profile !== profile || currentOwner?.connectionId !== connection) {
        return { available: false, reason: 'Focused account changed. Refresh this chat.' }
      }
      let data
      try {
        data = await ctx.rest(`/usage?session_id=${encodeURIComponent(sessionId)}&expected_profile=${encodeURIComponent(profile)}`, { timeoutMs: 40000 })
      } catch {
        throw new Error('Quota could not refresh. If just installed, restart Hermes Desktop once to load the backend.')
      }
      if (data?.session_id !== sessionId || data?.profile !== profile) {
        return { available: false, reason: 'The backend could not verify this focused session.' }
      }
      return data
    },
    staleTime: 120000,
    refetchInterval: 180000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    retry: false,
    gcTime: 300000,
  })
  const scopeReason = !sessionId ? 'Open a chat and send a message to initialize its account.'
    : connection !== 'local' ? 'Quota display is available for local Hermes Desktop sessions.'
    : !scoped ? 'Select this profile as the active workspace to read its quota safely.'
    : gateway !== 'open' ? 'Waiting for the Hermes backend connection.' : null
  const data = scopeReason ? null : query.data
  const view = quotaView(data)
  const expired = !!data?.fetched_at && Date.now() - Date.parse(data.fetched_at) > 300000
  const stale = !!data?.available && (query.isError || expired)
  const loading = !scopeReason && query.isPending && query.isFetching
  const label = loading ? 'Codex · checking…' : `${view.label}${stale ? ' · stale' : ''}`
  const reason = scopeReason || (query.isError ? query.error.message : view.reason)

  return jsxs(Popover, {
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsx('button', {
          type: 'button', 'data-codex-quota': 'chip',
          'aria-label': `${label}. Open subscription quota details`,
          title: 'Codex subscription allowance — not context-window usage',
          style: { padding: '0 8px', fontSize: '11px', whiteSpace: 'nowrap', color: 'var(--ui-text-secondary)', cursor: 'pointer' },
          children: label,
        }),
      }),
      jsxs(PopoverContent, {
        side: 'top', align: 'end',
        style: { width: '320px', padding: '16px', fontSize: '12px' },
        children: [
          jsx('div', { style: { fontWeight: 600, fontSize: '14px', marginBottom: '4px' }, children: 'Codex subscription quota' }),
          jsx('div', { style: muted, children: `${profile || 'No profile'}${data?.plan ? ` · ${data.plan}` : ''}` }),
          reason ? jsx('p', { role: 'status', style: { ...muted, marginTop: '12px', lineHeight: 1.5 }, children: reason }) : null,
          stale ? jsx('p', { role: 'status', children: 'Showing an older reading. Refresh to confirm your allowance.' }) : null,
          ...view.windows.map((window, i) => jsxs('div', {
            style: { marginTop: '14px' },
            children: [
              jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', gap: '8px' }, children: [
                jsx('span', { children: window.label }),
                jsx('strong', { children: window.remaining === null ? 'Unavailable' : `${window.remaining}% remaining` }),
              ] }),
              window.remaining === null ? null : jsx('div', {
                role: 'progressbar', 'aria-label': `${window.label} remaining allowance`,
                'aria-valuenow': window.remaining, 'aria-valuemin': 0, 'aria-valuemax': 100,
                style: { height: '5px', borderRadius: '3px', background: 'var(--ui-stroke-secondary)', overflow: 'hidden', margin: '8px 0' },
                children: jsx('div', { style: { height: '100%', width: `${window.remaining}%`, background: 'var(--ui-accent)' } }),
              }),
              jsx('div', { style: muted, children: window.remaining === null ? 'Usage not reported' : `${Math.round(window.used_percent)}% used` }),
              jsx('div', { style: { ...muted, marginTop: '3px' }, children: `Resets: ${timestamp(window.reset_at)}` }),
            ],
          }, `${window.label}-${i}`)),
          ...(data?.details || []).map((detail, i) => jsx('p', { style: { ...muted, lineHeight: 1.4 }, children: detail }, `detail-${i}`)),
          jsx('p', { style: { ...muted, marginTop: '14px', fontSize: '11px' }, children: `Last refreshed: ${timestamp(data?.fetched_at)}` }),
          jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }, children: [
            jsx('span', { style: { ...muted, fontSize: '11px' }, children: 'Auto-refresh every 3 minutes' }),
            jsx(Button, {
              size: 'sm', variant: 'outline', 'data-codex-quota': 'refresh',
              disabled: !scoped || gateway !== 'open' || query.isFetching,
              onClick: () => { void query.refetch() },
              children: query.isFetching ? 'Refreshing…' : 'Refresh',
            }),
          ] }),
          jsx('p', { style: { ...muted, fontSize: '11px', lineHeight: 1.4, marginBottom: 0 }, children: 'Subscription allowance, not chat context. Quota reads never redeem banked resets.' }),
        ],
      }),
    ],
  })
}

export default {
  id: 'codex-quota', name: 'Codex Quota', defaultEnabled: true,
  register(ctx) {
    ctx.register({ id: 'status', area: STATUSBAR_AREAS.right, order: 135,
      render: () => jsx(QuotaStatus, { ctx }) })
  },
}
