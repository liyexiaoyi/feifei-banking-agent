/** Shared framing with backend build_identity.py. No git, secrets, or timestamps. */
import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync, readdirSync, lstatSync } from 'node:fs'
import { resolve, relative, join, dirname, basename } from 'node:path'
import { fileURLToPath } from 'node:url'

const configs = ['package.json','package-lock.json','tsconfig.json','index.html','vite.config.ts','build-identity.mjs']
const posix = value => value.replaceAll('\\','/')
const sha = bytes => createHash('sha256').update(bytes).digest()
function walk(folder,ignoreCache=false) {
  const stat = lstatSync(folder)
  if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error('Missing directory or symbolic link')
  return readdirSync(folder).flatMap(name => {
    const path=join(folder,name), info=lstatSync(path)
    if(info.isSymbolicLink()) throw new Error('Symbolic links are not supported')
    return info.isDirectory() ? (ignoreCache && name==='__pycache__'?[]:walk(path,ignoreCache)) : info.isFile()?[path]:[]
  })
}
function digest(root, files, text=true) {
  if(!files.length)throw new Error('Empty fingerprint input')
  const result=createHash('sha256')
  const ordered=files.sort((a,b)=>Buffer.compare(Buffer.from(posix(relative(root,a))),Buffer.from(posix(relative(root,b)))))
  for(const file of ordered){
    for(let part=file;;part=dirname(part)){
      if(lstatSync(part).isSymbolicLink())throw new Error('Symbolic path components are not supported')
      if(part===root)break
      if(dirname(part)===part)throw new Error('Fingerprint path outside root')
    }
    const stat=lstatSync(file)
    if(stat.isSymbolicLink() || !stat.isFile())throw new Error('Missing file or symbolic link')
    let bytes=readFileSync(file)
    if(text)bytes=Buffer.from(new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes).replaceAll('\r\n','\n'))
    result.update(posix(relative(root,file))+'\0').update(sha(bytes)).update('\0')
  }
  return result.digest('hex')
}
export function sourceIds(root){
  const backRoot=join(root,'backend/ff_agent')
  const back=walk(backRoot,true).filter(p=>p.endsWith('.py') || (p.endsWith('.txt') && posix(relative(backRoot,p)).split('/').includes('prompts')))
  const front=walk(join(root,'frontend/src'),true).filter(p=>!(/\.(test|spec)\./.test(basename(p))) && basename(p)!=='component-test-loader.mjs')
  return {backend_id:digest(root,[...back,join(root,'requirements.lock.txt')]),frontend_id:digest(root,[...front,...configs.map(n=>join(root,'frontend',n))])}
}
export function assetsId(dist){return digest(dist,walk(dist).filter(p=>posix(relative(dist,p))!=='build-meta.json'),false)}
export function writeMeta(root,dist,ids){
  const current=sourceIds(root)
  if(current.backend_id!==ids.backend_id || current.frontend_id!==ids.frontend_id)throw new Error('Source changed during build; rebuild required')
  const meta={schema:1,...ids,assets_id:assetsId(dist)}
  writeFileSync(join(dist,'build-meta.json'),JSON.stringify(meta)+'\n')
}
if(process.argv[1] && resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const root=resolve(process.argv[2]); console.log(JSON.stringify({...sourceIds(root),assets_id:assetsId(join(root,'frontend/dist'))}))
}
