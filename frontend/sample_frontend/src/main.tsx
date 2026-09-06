import React from 'react';
import { createRoot } from 'react-dom/client';
import { ThemeProvider } from './stores/theme';
import { AuthProvider } from './stores/auth';
import { PlayerProvider } from './stores/player';
import { ToastProvider } from './stores/toast';
import { SubscriptionProvider } from './stores/subscription';
import { AppRouter } from './app/router';
import './styles/globals.css';

const container = document.getElementById('root');
if (!container) throw new Error('Root container missing');

const root = createRoot(container);
root.render(
  <React.StrictMode>
    <ThemeProvider>
      <ToastProvider>
        <AuthProvider>
          <SubscriptionProvider>
            <PlayerProvider>
              <AppRouter />
            </PlayerProvider>
          </SubscriptionProvider>
        </AuthProvider>
      </ToastProvider>
    </ThemeProvider>
  </React.StrictMode>
);
