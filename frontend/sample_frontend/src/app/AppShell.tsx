import { useState, useEffect } from 'react';
import { useAuth } from '../stores/auth';
import { BottomNav } from '../components/navigation/BottomNav';
import { MiniPlayer } from '../components/feed/MiniPlayer';
import { NetworkBanner } from '../components/common/NetworkBanner';
import { OnboardingModal } from '../components/feed/OnboardingModal';
import { shareAPI } from '../api/client';
import { isDemoMode } from '../data/feedAdapter';

interface Props { page: string; children: React.ReactNode; }

export function AppShell({ page, children }: Props) {
  const { authed } = useAuth();

  const [unread, setUnread] = useState(0);
  const [onboarding, setOnboarding] = useState(false);
  const demo = isDemoMode();

  useEffect(() => {
    if (!authed) return;
    const poll = () => {
      if (demo) return;
      shareAPI.getUnread().then(d => setUnread(d.unread || 0)).catch(() => {});
    };
    poll();
    const id = setInterval(poll, 30000);
    return () => clearInterval(id);
  }, [authed, demo]);

  useEffect(() => {
    if (authed && sessionStorage.getItem('ef_new_user') === '1') {
      sessionStorage.removeItem('ef_new_user');
      setOnboarding(true);
    }
  }, [authed]);

  const go = (p: string) => { window.location.href = '/' + p; };

  return (
    <>
      <div className="scan-line" />
      <NetworkBanner />
      <div key={page} className="page-in" style={{ maxWidth: 470, margin: '0 auto', minHeight: '100vh', position: 'relative' }}>
        {children}
      </div>
      <MiniPlayer />
      <BottomNav page={page} go={go} unread={unread} />
      {onboarding && <OnboardingModal onDone={() => setOnboarding(false)} />}
    </>
  );
}
