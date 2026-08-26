import { useState, useEffect } from 'react';
import { AudioClip } from '../types';
import { usePlayer } from '../stores/player';
import { useToast } from '../stores/toast';
import { ReelList } from '../components/feed/ReelList';
import { FeedSkeleton } from '../components/common/molecules';
import { fetchFeed } from '../data/feedAdapter';
import { isDemoMode } from '../data/feedAdapter';

interface Props { go: (p: string, params?: Record<string, unknown>) => void; }

export function FeedPage({ go }: Props) {
  const toast = useToast();
  const { active } = usePlayer();
  const [clips, setClips] = useState<AudioClip[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [initial, setInitial] = useState(true);
  const [hasMore, setHasMore] = useState(true);
  const demo = isDemoMode();

  const load = async (isInitial = false) => {
    if (isInitial) { setInitial(true); }
    setLoading(true); setErr(null);
    try {
      const { clips: fresh, hasMore: hm, err: e } = await fetchFeed();
      if (e) throw new Error(e);
      setClips(p => [...p, ...fresh]);
      setHasMore(hm);
       if (isInitial && fresh.length === 0) toast('Queue is empty — check back soon', 'info');
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Error';
      setErr(msg);
      if (isInitial) toast('Could not load feed: ' + msg, 'error');
    } finally {
      setLoading(false);
      setInitial(false);
    }
  };

  useEffect(() => { load(true);   }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const loadMore = () => { if (!loading && hasMore) { load(false); } };

  return (
    <div>
      <div style={{ padding: '56px 14px 4px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{
            fontFamily: 'var(--font-display)', fontSize: 30, fontWeight: 900,
            letterSpacing: '0.04em', lineHeight: 1, color: 'var(--on-surface)'
          }}>FOR YOU</h1>
          <p style={{ fontSize: 11, color: 'var(--outline)', letterSpacing: '0.06em', marginTop: 2 }}>
            {demo ? 'DEMO — connect backend for real recommendations' : 'PERSONALIZED AUDIO FEED'}
          </p>
        </div>
        {demo && active && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--terracotta)', fontWeight: 600 }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--terracotta)', animation: 'pulse-soft 2s infinite' }} />
            DEMO
          </div>
        )}
      </div>
      {loading && initial
        ? <FeedSkeleton />
        : <ReelList
            clips={clips} loading={loading && !initial} err={err} retry={() => { setClips([]); setInitial(true); load(true); }}
            hasMore={hasMore} loadMore={loadMore} onProfileClick={(id: number) => go('profile', { userId: id })}
          />
      }
    </div>
  );
}
