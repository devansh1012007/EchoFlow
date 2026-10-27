import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.tsx';
import { TermsPage, TERMS_PATH } from './pages/Terms.tsx';
import './index.css';

// Path-based routing without a router: /terms is public and renders outside
// the auth gate. `public/_redirects` serves index.html for every other path.
const onTerms = window.location.pathname.replace(/\/+$/, '') === TERMS_PATH;

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {onTerms ? <TermsPage /> : <App />}
  </StrictMode>,
);
