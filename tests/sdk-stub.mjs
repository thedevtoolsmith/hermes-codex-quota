import React, { createContext, useContext, useState, useSyncExternalStore } from 'react'
export { useQuery, QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'

function atom(value) {
  const listeners = new Set()
  return { get: () => value, set(next) { value = next; listeners.forEach(fn => fn()) },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) } }
}
export const host = {
  state: {
    focusedSessionId: atom('runtime-1'), focusedSessionProfile: atom('default'),
    focusedSessionOwner: atom({ connectionId: 'local', profile: 'default' }),
    connectionId: atom('local'), profile: atom('default'), model: atom('gpt-codex'), gateway: atom('open'),
  },
  onEvent() { return () => {} },
}
export const useValue = state => useSyncExternalStore(state.subscribe, state.get, state.get)
export const haptic = () => {}
export const STATUSBAR_AREAS = { right: 'statusBar.right' }
const PopoverState = createContext(null)
export function Popover({ children }) {
  const [open, setOpen] = useState(false)
  return React.createElement(PopoverState.Provider, { value: { open, setOpen } }, children)
}
export function PopoverTrigger({ children }) {
  const { open, setOpen } = useContext(PopoverState)
  return React.cloneElement(children, { onClick: () => setOpen(!open) })
}
export function PopoverContent({ children, side, align, ...props }) {
  const { open } = useContext(PopoverState)
  return open ? React.createElement('div', { role: 'dialog', ...props }, children) : null
}
export const Button = ({ children, variant, size, ...props }) => React.createElement('button', props, children)
