import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router'
import { AuthGate } from './components/AuthGate'
import { Dashboard } from './pages/Dashboard'
import { FindSenders } from './pages/FindSenders'
import { Settings } from './pages/Settings'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthGate>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/settings/senders" element={<FindSenders />} />
          <Route path="*" element={<Dashboard />} />
        </Routes>
      </BrowserRouter>
    </AuthGate>
  </StrictMode>,
)
