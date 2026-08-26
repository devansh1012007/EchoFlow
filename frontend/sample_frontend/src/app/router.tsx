import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom';
import { useEffect } from 'react';
import { useAuth } from '../stores/auth';
import { useBackendStatus } from '../hooks/useBackendStatus';
import { LoginPage } from '../pages/Login';
import { FeedPage } from '../pages/Feed';
import { ExplorePage } from '../pages/Explore';
import { ProfilePage } from '../pages/Profile';
import { InboxPage } from '../pages/Inbox';
import { LibraryPage } from '../pages/Library';
import { UploadPage } from '../pages/Upload';
import { SettingsPage } from '../pages/Settings';
import { DeveloperDemoPage } from '../pages/DeveloperDemo';
import { AppShell } from './AppShell';
import { setBackendStatus } from '../data/feedAdapter';

function RequireAuth({ children }: { children: JSX.Element }) {
  const { authed } = useAuth();
  if (!authed) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginRouter />} />
        <Route path="/dev/demo" element={<Protected><AppShell page="devdemo"><DeveloperDemoPage go={(p) => navTo(p)} /></AppShell></Protected>} />
        <Route path="/" element={<RootRedirect />} />
        <Route path="/feed" element={<Protected><AppShell page="feed"><FeedPage go={navTo} /></AppShell></Protected>} />
        <Route path="/explore" element={<Protected><AppShell page="explore"><ExplorePage go={navTo} /></AppShell></Protected>} />
        <Route path="/profile" element={<Protected><AppShell page="profile"><ProfilePageWrapper /></AppShell></Protected>} />
        <Route path="/profile/:userId" element={<Protected><AppShell page="profile"><ProfilePageWrapper /></AppShell></Protected>} />
        <Route path="/inbox" element={<Protected><AppShell page="inbox"><InboxPage go={navTo} /></AppShell></Protected>} />
        <Route path="/library" element={<Protected><AppShell page="library"><LibraryPage go={navTo} /></AppShell></Protected>} />
        <Route path="/upload" element={<Protected><AppShell page="upload"><UploadPage go={navTo} /></AppShell></Protected>} />
        <Route path="/settings" element={<Protected><AppShell page="settings"><SettingsPage go={navTo} /></AppShell></Protected>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <BackendWatcher />
    </BrowserRouter>
  );
}

function navTo(p: string, params?: Record<string, unknown>) {
  const q = params && Object.keys(params).length ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
  window.location.href = '/' + p + q;
}

function RootRedirect() {
  const { authed } = useAuth();
  return authed ? <Navigate to="/feed" replace /> : <Navigate to="/login" replace />;
}

function LoginRouter() {
  return <LoginPage onSuccess={(p) => navTo(p === 'explore' ? 'explore' : 'feed')} />;
}

function ProfilePageWrapper() {
  const { userId } = useParams();
  const go = (p: string) => { window.location.href = '/' + p; };
  return <ProfilePage go={go} userId={userId ? Number(userId) : undefined} />;
}

function Protected({ children }: { children: JSX.Element }) {
  return <RequireAuth>{children}</RequireAuth>;
}

function BackendWatcher() {
  const status = useBackendStatus();
  useEffect(() => {
    if (status !== null) setBackendStatus(!!status);
  }, [status]);
  return null;
}
