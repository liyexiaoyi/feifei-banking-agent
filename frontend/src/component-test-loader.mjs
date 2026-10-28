/** Node-only loader for pure JSX unit tests; TypeScript is an existing dependency. */
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import ts from 'typescript'

const here=dirname(fileURLToPath(import.meta.url))
const cache=join(here,'..','.cache','component-tests')
const loaded=new Map()
export async function loadComponent(name){
  if(!/^[A-Za-z][A-Za-z0-9]+\.tsx$/.test(name))throw new Error('Unexpected component name')
  if(loaded.has(name))return loaded.get(name)
  const source=await readFile(join(here,name),'utf8')
  const compiled=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
    .replace(/^import\s+['"][^'"]+\.css['"];?\s*$/gm,'')
  await mkdir(cache,{recursive:true})
  const target=join(cache,name.replace('.tsx','.mjs'))
  await writeFile(target,compiled)
  const result=await import(pathToFileURL(target).href+'?unit='+Date.now())
  loaded.set(name,result)
  return result
}

export function elements(tree,type){
  if(Array.isArray(tree))return tree.flatMap(item=>elements(item,type))
  if(!tree||typeof tree!=='object')return []
  return [...(tree.type===type?[tree]:[]),...elements(tree.props?.children,type)]
}

export function textContent(tree){
  if(Array.isArray(tree))return tree.map(textContent).join('')
  if(typeof tree==='string'||typeof tree==='number')return String(tree)
  return tree?.props?textContent(tree.props.children):''
}
