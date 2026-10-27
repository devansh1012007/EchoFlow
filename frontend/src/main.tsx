import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.tsx';
import { resolvePublicRoute } from './lib/publicRoutes.tsx';
import './index.css';

// Path-based routing without a router: a few public pages (/terms, /support,
// shared clips) render outside the auth gate. `public/_redirects` serves
// index.html for every path, and anything unmatched falls through to <App />.
const publicPage = resolvePublicRoute(window.location.pathname, window.location.search);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {publicPage ?? <App />}
  </StrictMode>,
);
