import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { Play } from 'lucide-react-native';

import { getPublicClip, playSharedClip, type PublicClip } from '../../src/api/endpoints/clips';
import { categoryColor, categoryLabel } from '../../src/design/categories';
import { accent, border, content, spacing, surface } from '../../src/design/tokens';
import { typography } from '../../src/design/typography';
import { loadClip } from '../../src/store/player';

/** Anonymous recipient route for https://app.echoflow.in/clip/:id?s=:shareToken. */
export default function SharedClipScreen() {
  const { id, s } = useLocalSearchParams<{ id?: string; s?: string }>();
  const [clip, setClip] = useState<PublicClip | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!id) { setError('This share link is incomplete.'); setLoading(false); return; }
    let active = true;
    void getPublicClip(id).then((next) => { if (active) setClip(next); }).catch(() => { if (active) setError('This clip is unavailable.'); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id]);

  const startPlayback = async () => {
    if (!clip || !id || !s || playing) { if (!s) setError('This share link is missing its access token.'); return; }
    setPlaying(true); setError(null);
    try {
      const playback = await playSharedClip(id, s);
      // Public metadata intentionally omits creator_id. The player only owns
      // media state, so use a non-routable sentinel rather than exposing or
      // inventing an account identifier in the public API contract.
      await loadClip({ ...clip, duration_ms: clip.duration_ms ?? undefined, creator_id: 0, hls_playlist_url: playback.hlsPlaylistUrl, likes: 0, shares: 0, skips: 0, comment_count: 0, is_liked: false }, playback.token);
    } catch { setError('Playback could not start. This link may have expired.'); } finally { setPlaying(false); }
  };

  if (loading) return <View style={styles.center}><ActivityIndicator color={accent.base} /><Text style={styles.body}>Opening shared clip…</Text></View>;
  if (!clip) return <View style={styles.center}><Text style={styles.title}>Clip unavailable</Text><Text style={styles.body}>{error ?? 'This link is no longer available.'}</Text></View>;
  const color = categoryColor(clip.category);
  return <View style={styles.screen}>
    {clip.cover_image ? <Image source={{ uri: clip.cover_image }} style={styles.cover} accessibilityLabel={`Cover artwork for ${clip.title}`} /> : <View style={[styles.cover, styles.fallback, { borderColor: color }]}><Text style={[styles.category, { color }]}>{categoryLabel(clip.category)}</Text></View>}
    <Text style={styles.eyebrow}>Shared from EchoFlow</Text><Text style={styles.title}>{clip.title}</Text><Text style={styles.creator}>{clip.creator_name}</Text>
    <Pressable accessibilityRole="button" accessibilityLabel={`Play ${clip.title} by ${clip.creator_name}`} accessibilityHint="Starts this shared clip" accessibilityState={{ busy: playing }} onPress={() => void startPlayback()} style={styles.play}><Play size={24} color={surface.base} fill={surface.base} /><Text style={styles.playText}>{playing ? 'Starting…' : 'Play clip'}</Text></Pressable>
    {error ? <Text accessibilityLiveRegion="polite" style={styles.error}>{error}</Text> : null}
  </View>;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: surface.base, padding: spacing.stack, justifyContent: 'center', gap: spacing.gutter }, center: { flex: 1, backgroundColor: surface.base, alignItems: 'center', justifyContent: 'center', padding: spacing.stack, gap: spacing.gutter }, cover: { width: '100%', aspectRatio: 1, maxHeight: 320, borderRadius: 20, backgroundColor: surface.containerHighest }, fallback: { alignItems: 'center', justifyContent: 'center', borderWidth: 1 }, eyebrow: { ...typography.microLabel, color: accent.base }, title: { ...typography.page, color: content.primary }, creator: { ...typography.bodySecondary, color: content.secondary }, body: { ...typography.bodySecondary, color: content.tertiary, textAlign: 'center' }, category: { ...typography.label }, play: { minHeight: 56, borderRadius: 16, backgroundColor: accent.base, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10 }, playText: { ...typography.label, color: surface.base }, error: { ...typography.bodySecondary, color: '#ffb4ab', textAlign: 'center' },
});
