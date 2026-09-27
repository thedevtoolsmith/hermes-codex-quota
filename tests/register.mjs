import { registerHooks, createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'

if (!process.env.HERMES_SOURCE) throw new Error('Set HERMES_SOURCE to the Hermes source checkout for tests')
const require = createRequire(`${process.env.HERMES_SOURCE}/package.json`)
const aliases = new Map(['react', 'react/jsx-runtime', 'react-dom/client', '@tanstack/react-query', 'jsdom']
  .map(name => [name, pathToFileURL(require.resolve(name)).href]))
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '@hermes/plugin-sdk') {
      return { url: new URL('./sdk-stub.mjs', import.meta.url).href, shortCircuit: true }
    }
    if (aliases.has(specifier)) {
      return { url: aliases.get(specifier), shortCircuit: true }
    }
    return nextResolve(specifier, context)
  },
})
