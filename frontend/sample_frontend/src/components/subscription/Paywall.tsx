import { useState, useEffect } from 'react';
import { subscriptionAPI } from '../../api/client';
import { useAuth } from '../../stores/auth';

interface Props {
  onDone?: () => void;
}

export function Paywall({ onDone }: Props) {
  const { user } = useAuth();
  const [manageUrl, setManageUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadManageUrl = async () => {
    try {
      const d = await subscriptionAPI.getManageUrl();
      setManageUrl(d.url);
    } catch (e: any) {
      setError(e.message || 'Failed to load management URL');
    }
  };

  const handleUpgrade = async () => {
    setLoading(true);
    try {
      await subscriptionAPI.sync();
      await loadManageUrl();
      if (manageUrl || true) {
        const url = manageUrl || (await subscriptionAPI.getManageUrl()).url;
        window.location.href = url;
      }
    } catch (e: any) {
      setError(e.message || 'Failed to process upgrade');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadManageUrl();
  }, []);

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 9999,
    }}>
      <div style={{
        background: 'var(--surface-container)', borderRadius: 24,
        padding: 32, maxWidth: 400, width: '90%', textAlign: 'center',
        border: '1px solid var(--outline-variant)',
      }}>
        <div style={{
          width: 64, height: 64, borderRadius: '50%',
          background: 'var(--primary-container)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          margin: '0 auto 20px',
        }}>
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--on-primary-container)" strokeWidth="2">
            <path d="M12 2L2 7v10c0 5 6 9 10 9s10-4 10-9V7l-10-5z" />
            <path d="M12 12l8-4v6c0 4-5 8-8 8s-8-4-8-8V8l8 4z" />
          </svg>
        </div>
        <h2 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 8px', fontFamily: 'var(--font-display)' }}>
          Go Pro
        </h2>
        <p style={{ fontSize: 14, color: 'var(--on-surface-variant)', margin: '0 0 24px', lineHeight: 1.5 }}>
          Unlimited uploads, longer clips (up to 5 min), higher audio quality, and no daily limits.
        </p>

        {error && (
          <p style={{ color: 'var(--error)', fontSize: 12, margin: '0 0 16px' }}>{error}</p>
        )}

        <button
          onClick={handleUpgrade}
          disabled={loading}
          style={{
            width: '100%', padding: '14px 24px', borderRadius: 'var(--radius-full)',
            fontSize: 16, fontWeight: 700, border: 'none', cursor: loading ? 'default' : 'pointer',
            background: 'var(--terracotta)', color: '#000',
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? 'Processing...' : 'Upgrade to Pro'}
        </button>

        {onDone && (
          <button
            onClick={onDone}
            style={{
              marginTop: 16, width: '100%', padding: '10px',
              background: 'transparent', color: 'var(--on-surface-variant)',
              border: 'none', fontSize: 13, cursor: 'pointer',
            }}
          >
            Maybe later
          </button>
        )}
      </div>
    </div>
  );
}
