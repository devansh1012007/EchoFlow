import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, Image, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { Heart, MessageCircle, Pause, Play, Radio, Share2 } from 'lucide-react-native';

import type { FeedClip } from '../../src/api/schema';
import { NetworkBanner } from '../../src/components/NetworkBanner';
import { CommentSheet } from '../../src/components/comments/CommentSheet';
import { ShareModal } from '../../src/components/share/ShareModal';
import { ALL_CATEGORIES, categoryColor, categoryLabel } from '../../src/design/categories';
import { accent, border, content, spacing, surface } from '../../src/design/tokens';
import { typography } from '../../src/design/typography';
import { useBackendStatus } from '../../src/hooks/useBackendStatus';
import { useSuggestions } from '../../src/hooks/useSuggestions';
import { mintPlaybackToken } from '../../src/api/endpoints/feed';
import { toggleLike } from '../../src/api/endpoints/interactions';
import { formatTime } from '../../src/lib/formatTime';
import { loadClip, pause, resume, usePlayerStore } from '../../src/store/player';
import { useAuthStore } from '../../src/store/auth';

const DISCOVER_CATEGORIES = ['all', ...ALL_CATEGORIES] as const;

/** A paged, non-destructive discovery surface, separate from the reel queue. */
export default function Screen() {
  const backend = useBackendStatus();
  const viewerId = useAuthStore((state) => state.user?.id ?? null);
  const [category, setCategory] = useState<string>('all');
  const suggestions = useSuggestions(category);
  const [pendingClipId, setPendingClipId] = useState<string | null>(null);
  const [commentClip, setCommentClip] = useState<FeedClip | null>(null);
  const [shareClip, setShareClip] = useState<FeedClip | null>(null);
  const playingClipId = usePlayerStore((state) => state.playingClipId);
  const playback = usePlayerStore((state) => state.playback);
  const setQueue = usePlayerStore((state) => state.setQueue);
  const setActiveIndex = usePlayerStore((state) => state.setActiveIndex);
  const setCardStatus = usePlayerStore((state) => state.setCardStatus);

  const playClip = useCallback(async (clip: FeedClip) => {
    if (pendingClipId) return;
    if (playingClipId === clip.id) {
      if (playback === 'playing') pause(); else resume();
      return;
    }
    setPendingClipId(clip.id);
    setCardStatus('minting');
    setQueue([clip]);
    setActiveIndex(0);
    try {
      const { token } = await mintPlaybackToken(clip.id);
      await loadClip(clip, token);
    } catch (cause) {
      setCardStatus('error', cause instanceof Error ? cause.message : 'Could not start playback.');
    } finally {
      setPendingClipId(null);
    }
  }, [pendingClipId, playback, playingClipId, setActiveIndex, setCardStatus, setQueue]);

  const selection = category === 'all' ? 'All categories' : categoryLabel(category);
  const heading = suggestions.personalized ? 'For you' : 'Fresh picks';
  return <View style={styles.screen}>
    <NetworkBanner status={backend} />
    <View style={[styles.content, backend === 'offline' && styles.contentWithBanner]}>
      <Text style={styles.title}>Discover</Text>
      <FlatList horizontal data={DISCOVER_CATEGORIES} keyExtractor={(item) => item} contentContainerStyle={styles.pills} showsHorizontalScrollIndicator={false}
        renderItem={({ item }) => {
          const selected = item === category;
          const label = item === 'all' ? 'All' : categoryLabel(item);
          return <Pressable accessibilityRole="button" accessibilityState={{ selected }} accessibilityLabel={`Filter Discover by ${label}`} accessibilityHint={`Shows ${label} clips`} onPress={() => setCategory(item)} style={[styles.pill, selected && styles.pillSelected]}><Text style={[typography.label, selected && styles.pillLabelSelected]}>{label}</Text></Pressable>;
        }} />
      <View style={styles.sectionHeading}><Text style={styles.heading}>{heading}</Text><Text style={styles.selectedCategory}>{selection}</Text></View>
      {suggestions.loading ? <DiscoverSkeleton /> : suggestions.error ? <View style={styles.center}><Text style={styles.body}>{suggestions.error}</Text><Pressable accessibilityRole="button" accessibilityLabel="Retry Discover" onPress={suggestions.refresh} style={styles.retry}><Text style={typography.label}>Retry</Text></Pressable></View> : <FlatList
        testID="discover-list" data={suggestions.clips} keyExtractor={(item) => item.id}
        renderItem={({ item }: { item: FeedClip }) => <DiscoverCard clip={item} active={playingClipId === item.id} playing={playingClipId === item.id && playback === 'playing'} loading={pendingClipId === item.id} onPlay={() => void playClip(item)} onOpenComments={() => setCommentClip(item)} onOpenShare={() => setShareClip(item)} onOpenProfile={() => router.push({ pathname: '/profile/[id]', params: { id: String(item.creator_id) } })} />}
        contentContainerStyle={suggestions.clips.length ? styles.list : styles.center} refreshControl={<RefreshControl refreshing={suggestions.refreshing} onRefresh={suggestions.refresh} tintColor={accent.base} />} onEndReached={suggestions.loadMore} onEndReachedThreshold={0.6}
        ListEmptyComponent={<Text style={styles.body}>No eligible clips in {selection.toLowerCase()} yet.</Text>} ListFooterComponent={suggestions.loadingMore ? <ActivityIndicator color={accent.base} /> : null} />}
    </View>
    <ShareModal visible={shareClip !== null} clipId={shareClip?.id ?? ''} title={shareClip?.title} creatorName={shareClip?.creator_name} isShareable onClose={() => setShareClip(null)} />
    <CommentSheet visible={commentClip !== null} clipId={commentClip?.id ?? ''} viewerId={viewerId} onClose={() => setCommentClip(null)} />
  </View>;
}

function DiscoverCard({ clip, active, playing, loading, onPlay, onOpenProfile, onOpenComments, onOpenShare }: { clip: FeedClip; active: boolean; playing: boolean; loading: boolean; onPlay: () => void; onOpenProfile: () => void; onOpenComments: () => void; onOpenShare: () => void }) {
  const category = categoryLabel(clip.category);
  const duration = clip.duration_ms ? formatTime(clip.duration_ms / 1000) : null;
  const action = playing ? 'Pause' : 'Play';
  const description = `${clip.title} by ${clip.creator_name}, ${category}${duration ? `, ${duration}` : ''}`;
  return <View style={[styles.card, active && styles.cardActive]}>
    <Pressable accessibilityRole="button" accessibilityLabel={`${action} ${description}`} accessibilityHint="Starts or pauses playback" accessibilityState={{ busy: loading }} disabled={loading} onPress={onPlay} style={styles.cardPrimary}>
      {clip.cover_image ? <Image source={{ uri: clip.cover_image }} style={styles.cover} accessibilityLabel={`Cover artwork for ${clip.title}`} /> : <FallbackArtwork color={categoryColor(clip.category)} category={category} />}
      <View style={styles.cardCopy}>
        <View style={styles.metaRow}><View style={[styles.categoryChip, { borderColor: categoryColor(clip.category) }]}><Text style={[styles.categoryText, { color: categoryColor(clip.category) }]}>{category}</Text></View>{duration ? <Text style={styles.duration}>{duration}</Text> : null}</View>
        <Text style={styles.cardTitle} numberOfLines={2}>{clip.title}</Text>
        {clip.tags?.length ? <Text style={styles.tags} numberOfLines={1}>{clip.tags.slice(0, 2).map((tag) => `#${tag}`).join('  ')}</Text> : null}
      </View>
    </Pressable>
    <View style={styles.cardFooter}><Pressable accessibilityRole="button" accessibilityLabel={`Open ${clip.creator_name}'s profile`} accessibilityHint="Opens the creator profile" onPress={onOpenProfile} hitSlop={8}><Text style={styles.creator}>{clip.creator_name}</Text></Pressable>{active ? <Text accessibilityLiveRegion="polite" style={styles.nowPlaying}>{playing ? 'Now playing' : 'Paused'}</Text> : null}</View>
    <View style={styles.cardControls}><Pressable accessibilityRole="button" accessibilityLabel={`${action} ${clip.title}`} accessibilityHint="Starts or pauses playback" accessibilityState={{ busy: loading }} disabled={loading} onPress={onPlay} style={styles.playButton}>{loading ? <ActivityIndicator color={surface.base} /> : playing ? <Pause size={22} color={surface.base} fill={surface.base} /> : <Play size={22} color={surface.base} fill={surface.base} />}</Pressable><DiscoverActions clip={clip} onOpenComments={onOpenComments} onOpenShare={onOpenShare} /></View>
  </View>;
}

function FallbackArtwork({ color, category }: { color: string; category: string }) {
  return <View accessibilityLabel={`${category} artwork`} style={[styles.cover, styles.fallback, { borderColor: color }]}><Radio size={30} color={color} /><Text style={[styles.fallbackText, { color }]} numberOfLines={1}>{category}</Text></View>;
}

function DiscoverActions({ clip, onOpenComments, onOpenShare }: { clip: FeedClip; onOpenComments: () => void; onOpenShare: () => void }) {
  const [liked, setLiked] = useState(clip.is_liked); const [likeCount, setLikeCount] = useState(clip.likes); const [pending, setPending] = useState(false);
  useEffect(() => { setLiked(clip.is_liked); setLikeCount(clip.likes); }, [clip.id, clip.is_liked, clip.likes]);
  const onLike = async () => { if (pending) return; const before = { liked, count: likeCount }; setPending(true); setLiked(!liked); setLikeCount((count) => Math.max(0, count + (liked ? -1 : 1))); try { const result = await toggleLike(clip.id); setLiked(result.status === 'liked'); } catch { setLiked(before.liked); setLikeCount(before.count); } finally { setPending(false); } };
  return <View style={styles.actions}><CompactAction label={liked ? 'Unlike' : 'Like'} hint="Adds or removes this clip from your likes" onPress={() => void onLike()} disabled={pending} icon={<Heart size={18} color={liked ? '#ffb4ab' : content.secondary} fill={liked ? '#ffb4ab' : 'none'} />} /><CompactAction label="Comments" hint="Opens comments for this clip" onPress={onOpenComments} icon={<MessageCircle size={18} color={content.secondary} />} /><CompactAction label="Share" hint="Shares this clip with an EchoFlow listener" onPress={onOpenShare} icon={<Share2 size={18} color={content.secondary} />} /></View>;
}

function CompactAction({ label, hint, icon, onPress, disabled = false }: { label: string; hint: string; icon: React.ReactNode; onPress: () => void; disabled?: boolean }) {
  return <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityHint={hint} accessibilityState={{ disabled }} onPress={onPress} disabled={disabled} style={({ pressed }) => [styles.compactAction, pressed && styles.pressed, disabled && styles.disabled]}>{icon}</Pressable>;
}

function DiscoverSkeleton() {
  return <View accessibilityLabel="Loading Discover" accessibilityRole="progressbar" style={styles.skeletonList}>{[0, 1, 2, 3].map((item) => <View key={item} style={styles.skeletonCard}><View style={styles.skeletonCover} /><View style={styles.skeletonCopy}><View style={styles.skeletonShort} /><View style={styles.skeletonLong} /><View style={styles.skeletonMedium} /></View></View>)}</View>;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: surface.base }, content: { flex: 1 }, contentWithBanner: { paddingTop: 58 }, center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.stack, gap: spacing.gutter }, title: { ...typography.page, color: content.primary, paddingHorizontal: spacing.stack, paddingTop: spacing.stack }, body: { ...typography.bodySecondary, color: content.tertiary, textAlign: 'center' }, pills: { gap: spacing.gutter, paddingHorizontal: spacing.stack, paddingVertical: spacing.gutter }, pill: { borderWidth: 1, borderColor: border.default, borderRadius: 999, paddingHorizontal: spacing.stack, paddingVertical: spacing.gutter }, pillSelected: { backgroundColor: accent.base, borderColor: accent.base }, pillLabelSelected: { color: surface.base }, sectionHeading: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', paddingHorizontal: spacing.stack, paddingBottom: spacing.gutter }, heading: { ...typography.title, color: content.primary }, selectedCategory: { ...typography.microLabel, color: content.tertiary, maxWidth: '48%', textAlign: 'right' }, list: { padding: spacing.stack, paddingTop: 0, gap: spacing.gutter }, card: { borderWidth: 1, borderColor: border.default, borderRadius: 16, padding: spacing.gutter, gap: spacing.gutter, backgroundColor: surface.containerLow }, cardActive: { borderColor: accent.base, backgroundColor: surface.container }, cardPrimary: { flexDirection: 'row', alignItems: 'center', gap: spacing.gutter }, cover: { width: 76, height: 76, borderRadius: 12, backgroundColor: surface.containerHighest }, fallback: { alignItems: 'center', justifyContent: 'center', borderWidth: 1, gap: 3 }, fallbackText: { ...typography.microLabel, maxWidth: 62, textAlign: 'center' }, cardCopy: { flex: 1, minWidth: 0, gap: 4 }, metaRow: { flexDirection: 'row', alignItems: 'center', gap: 8 }, categoryChip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2, maxWidth: '75%' }, categoryText: { ...typography.microLabel, fontSize: 10 }, duration: { ...typography.microLabel, color: content.tertiary }, cardTitle: { ...typography.label, color: content.primary }, cardFooter: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 }, creator: { ...typography.microLabel, color: accent.base }, nowPlaying: { ...typography.microLabel, color: accent.base }, tags: { ...typography.microLabel, color: content.tertiary }, cardControls: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.gutter }, playButton: { width: 52, height: 52, borderRadius: 16, alignItems: 'center', justifyContent: 'center', backgroundColor: accent.base }, actions: { flexDirection: 'row', gap: spacing.gutter }, compactAction: { minWidth: 48, minHeight: 48, alignItems: 'center', justifyContent: 'center', borderRadius: 12, borderWidth: 1, borderColor: border.default }, pressed: { opacity: 0.7, transform: [{ scale: 0.96 }] }, disabled: { opacity: 0.45 }, retry: { borderWidth: 1, borderColor: accent.base, borderRadius: 999, paddingHorizontal: spacing.stack, paddingVertical: spacing.gutter }, skeletonList: { padding: spacing.stack, paddingTop: 0, gap: spacing.gutter }, skeletonCard: { flexDirection: 'row', gap: spacing.gutter, padding: spacing.gutter, borderWidth: 1, borderColor: border.default, borderRadius: 16 }, skeletonCover: { width: 76, height: 76, borderRadius: 12, backgroundColor: surface.containerHighest }, skeletonCopy: { flex: 1, justifyContent: 'center', gap: 9 }, skeletonShort: { width: '30%', height: 10, borderRadius: 5, backgroundColor: surface.containerHighest }, skeletonMedium: { width: '50%', height: 10, borderRadius: 5, backgroundColor: surface.containerHighest }, skeletonLong: { width: '80%', height: 14, borderRadius: 7, backgroundColor: surface.containerHighest },
});
