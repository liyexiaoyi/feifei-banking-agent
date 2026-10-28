import { createRoot } from 'react-dom/client'
import App from './App'
import OperatorConsole from './OperatorConsole'
import './styles.css'
import './polish.css'
import './reading-mode.css'

createRoot(document.getElementById('root')!).render(window.location.pathname === '/operator' ? <OperatorConsole /> : <App />)
