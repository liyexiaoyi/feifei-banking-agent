import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { sourceIds, writeMeta } from './build-identity.mjs'

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..')
const ids=sourceIds(root)
let output=''
export default defineConfig({
  define:{__FF_BACKEND_ID__:JSON.stringify(ids.backend_id),__FF_FRONTEND_ID__:JSON.stringify(ids.frontend_id)},
  plugins:[react(),{name:'ff-build-identity',apply:'build',configResolved(config){output=resolve(config.root,config.build.outDir)},closeBundle(){writeMeta(root,output,ids)}}],
  server:{host:'127.0.0.1',proxy:{'/api':'http://127.0.0.1:8765'}}
})
