import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  RefreshControl,
  Dimensions,
  StyleSheet,
  ActivityIndicator,
  StatusBar,
  ViewToken,
} from 'react-native';
import {
  Heart,
  MessageSquare,
  Share2,
  SkipForward,
  Play,
  Pause,
  Headphones,
  Sparkles,
  Volume2,
} from 'lucide-react-native';
import * as Haptics from 'expo-haptics';
import { FeedClip } from '../types';
import { feedAPI, interactionsAPI } from '../services/api';
import { usePlayer } from '../context/PlayerContext';
import { AudioVisualizer } from '../components/AudioVisualizer';
import { CommentModal } from '../components/CommentModal';
import { ShareModal } from '../components/ShareModal';

const { height: SCREEN_HEIGHT, width: SCREEN_WIDTH } = Dimensions.get('window');
// Calculate active reel height taking into account status bar and bottom tabs
const REEL_HEIGHT = SCREEN_HEIGHT - 130;

export const FeedScreen = ({ navigation }: any) => {
  const [clips, setClips] = useState<FeedClip[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [currentIndex, setCurrentIndex] = useState<number>(0);

  // Modals
  const [commentClip, setCommentClip] = useState<FeedClip | null>(null);
  const [shareClip, setShareClip] = useState<FeedClip | null>(null);

  const {
    currentClip,
    isPlaying,
    positionMillis,
    durationMillis,
    handsFreeMode,
    setHandsFreeMode,
    playClip,
    togglePlayPause,
    skipNext,
    toggleLike,
    setQueue,
  } = usePlayer();

  const loadFeed = useCallback(async () => {
    try {
      const res = await feedAPI.getFeed();
      const items = res.results || [];
      setClips(items);
      setQueue(items);

      if (items.length > 0 && !currentClip) {
        playClip(items[0]);
      }
    } catch (err) {
      console.warn('Failed to load feed:', err);
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, [currentClip, playClip, setQueue]);

  useEffect(() => {
    loadFeed();
  }, [loadFeed]);

  // Pull-to-refresh handler
  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true);
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    } catch {}
    await loadFeed();
  }, [loadFeed]);

  // Handle visible item change during vertical scrolling
  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    if (viewableItems.length > 0) {
      const index = viewableItems[0].index;
      if (index !== null && index !== undefined && index !== currentIndex) {
        setCurrentIndex(index);
        const nextClip = clips[index];
        if (nextClip) {
          playClip(nextClip);
        }
      }
    }
  }).current;

  const viewabilityConfig = useRef({
    itemVisiblePercentThreshold: 70,
  }).current;

  const renderClipItem = ({ item, index }: { item: FeedClip; index: number }) => {
    const isCurrent = currentClip?.id === item.id;
    const progress = isCurrent && durationMillis > 0 ? positionMillis / durationMillis : 0;

    return (
      <View style={[styles.reelContainer, { height: REEL_HEIGHT }]}>
        {/* Ambient glow background */}
        <View style={styles.ambientGlow} />

        {/* Central Audio Artwork Card */}
        <View style={styles.centerArtWrapper}>
          <TouchableOpacity
            style={styles.artCircle}
            activeOpacity={0.9}
            onPress={togglePlayPause}
          >
            <View style={styles.artInner}>
              <AudioVisualizer isPlaying={isCurrent && isPlaying} height={60} barCount={24} />
            </View>

            {/* Play/Pause center overlay icon */}
            <View style={styles.playPauseOverlay}>
              {isCurrent && isPlaying ? (
                <Pause size={28} color="#000000" />
              ) : (
                <Play size={28} color="#000000" style={{ marginLeft: 3 }} />
              )}
            </View>
          </TouchableOpacity>

          {/* Progress bar */}
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${progress * 100}%` }]} />
          </View>
        </View>

        {/* Left Bottom Meta Info */}
        <View style={styles.bottomMeta}>
          <View style={styles.categoryBadge}>
            <Text style={styles.categoryText}>{item.category}</Text>
          </View>

          <Text style={styles.clipTitle} numberOfLines={2}>
            {item.title}
          </Text>

          <TouchableOpacity
            onPress={() => navigation.navigate('Profile', { userId: item.creator_id })}
            style={styles.creatorRow}
          >
            <View style={styles.creatorAvatar}>
              <Text style={styles.creatorAvatarText}>
                {item.creator_name?.slice(0, 1).toUpperCase()}
              </Text>
            </View>
            <Text style={styles.creatorName}>@{item.creator_name}</Text>
          </TouchableOpacity>
        </View>

        {/* Right Floating Actions Column (Like, Comment, Share, Skip) */}
        <View style={styles.rightActionsColumn}>
          {/* Like */}
          <TouchableOpacity
            style={styles.actionBtn}
            onPress={() => toggleLike(item.id)}
          >
            <View style={[styles.actionIconCircle, item.is_liked && styles.actionLikedCircle]}>
              <Heart
                size={22}
                color={item.is_liked ? '#FF2E63' : '#FFFFFF'}
                fill={item.is_liked ? '#FF2E63' : 'transparent'}
              />
            </View>
            <Text style={styles.actionCountText}>{item.likes || 0}</Text>
          </TouchableOpacity>

          {/* Comment */}
          <TouchableOpacity
            style={styles.actionBtn}
            onPress={() => setCommentClip(item)}
          >
            <View style={styles.actionIconCircle}>
              <MessageSquare size={22} color="#FFFFFF" />
            </View>
            <Text style={styles.actionCountText}>{item.comment_count || 0}</Text>
          </TouchableOpacity>

          {/* Share */}
          <TouchableOpacity
            style={styles.actionBtn}
            onPress={() => setShareClip(item)}
          >
            <View style={styles.actionIconCircle}>
              <Share2 size={22} color="#FFFFFF" />
            </View>
            <Text style={styles.actionCountText}>{item.shares || 0}</Text>
          </TouchableOpacity>

          {/* Skip */}
          <TouchableOpacity style={styles.actionBtn} onPress={skipNext}>
            <View style={[styles.actionIconCircle, styles.actionSkipCircle]}>
              <SkipForward size={22} color="#FF6321" />
            </View>
            <Text style={styles.actionSkipText}>SKIP</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  };

  if (isLoading) {
    return (
      <View style={styles.loadingScreen}>
        <StatusBar barStyle="light-content" />
        <ActivityIndicator size="large" color="#FF6321" />
        <Text style={styles.loadingTitle}>Connecting to EchoFlow Stream...</Text>
        <Text style={styles.loadingSub}>Pulling latest pgvector recommendations</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <StatusBar barStyle="light-content" />

      {/* Top Hands-Free Status Header */}
      <View style={styles.topBar}>
        <TouchableOpacity
          style={[styles.handsFreeToggle, handsFreeMode && styles.handsFreeActive]}
          onPress={() => setHandsFreeMode(!handsFreeMode)}
        >
          <Headphones size={15} color={handsFreeMode ? '#000000' : '#FF6321'} />
          <Text style={[styles.handsFreeText, handsFreeMode && styles.handsFreeActiveText]}>
            {handsFreeMode ? 'HANDS-FREE ON' : 'MANUAL'}
          </Text>
        </TouchableOpacity>

        <View style={styles.liveIndicator}>
          <View style={styles.liveDot} />
          <Text style={styles.liveText}>HNSW FEED</Text>
        </View>
      </View>

      {/* Reels Paging List with Pull-to-Refresh */}
      <FlatList
        data={clips}
        keyExtractor={(item) => item.id}
        renderItem={renderClipItem}
        pagingEnabled
        showsVerticalScrollIndicator={false}
        snapToInterval={REEL_HEIGHT}
        snapToAlignment="start"
        decelerationRate="fast"
        onViewableItemsChanged={onViewableItemsChanged}
        viewabilityConfig={viewabilityConfig}
        refreshControl={
          <RefreshControl
            refreshing={isRefreshing}
            onRefresh={handleRefresh}
            tintColor="#FF6321"
            colors={['#FF6321']}
            title="Synthesizing new audio stream..."
            titleColor="rgba(255,255,255,0.6)"
          />
        }
      />

      {/* Comment Sheet Modal */}
      {commentClip && (
        <CommentModal
          visible={!!commentClip}
          clipId={commentClip.id}
          clipTitle={commentClip.title}
          onClose={() => setCommentClip(null)}
        />
      )}

      {/* Share Sheet Modal */}
      {shareClip && (
        <ShareModal
          visible={!!shareClip}
          clip={shareClip}
          onClose={() => setShareClip(null)}
        />
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0A0A',
  },
  loadingScreen: {
    flex: 1,
    backgroundColor: '#0A0A0A',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  loadingTitle: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '800',
    textTransform: 'uppercase',
    marginTop: 16,
  },
  loadingSub: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 12,
    marginTop: 6,
  },
  topBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  handsFreeToggle: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(255,99,33,0.12)',
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(255,99,33,0.3)',
  },
  handsFreeActive: {
    backgroundColor: '#FF6321',
    borderColor: '#FF6321',
  },
  handsFreeText: {
    color: '#FF6321',
    fontSize: 10,
    fontWeight: '900',
    fontFamily: 'Courier',
  },
  handsFreeActiveText: {
    color: '#000000',
  },
  liveIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  liveDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: '#10B981',
  },
  liveText: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1,
  },
  reelContainer: {
    width: SCREEN_WIDTH,
    justifyContent: 'center',
    alignItems: 'center',
    position: 'relative',
  },
  ambientGlow: {
    position: 'absolute',
    width: 260,
    height: 260,
    borderRadius: 130,
    backgroundColor: 'rgba(255,99,33,0.06)',
    top: '20%',
  },
  centerArtWrapper: {
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 40,
  },
  artCircle: {
    width: 220,
    height: 220,
    borderRadius: 110,
    backgroundColor: '#141414',
    borderWidth: 2,
    borderColor: 'rgba(255,99,33,0.4)',
    alignItems: 'center',
    justifyContent: 'center',
    position: 'relative',
    shadowColor: '#FF6321',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.3,
    shadowRadius: 25,
    elevation: 8,
  },
  artInner: {
    width: 170,
    height: 170,
    borderRadius: 85,
    backgroundColor: 'rgba(255,255,255,0.03)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  playPauseOverlay: {
    position: 'absolute',
    bottom: -15,
    width: 50,
    height: 50,
    borderRadius: 25,
    backgroundColor: '#FF6321',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 6,
    elevation: 6,
  },
  progressTrack: {
    width: 220,
    height: 4,
    backgroundColor: 'rgba(255,255,255,0.1)',
    borderRadius: 2,
    marginTop: 28,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#FF6321',
  },
  bottomMeta: {
    position: 'absolute',
    left: 20,
    bottom: 24,
    right: 90,
  },
  categoryBadge: {
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(255,99,33,0.15)',
    borderWidth: 1,
    borderColor: 'rgba(255,99,33,0.3)',
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
    marginBottom: 8,
  },
  categoryText: {
    color: '#FF6321',
    fontSize: 10,
    fontWeight: '800',
    textTransform: 'uppercase',
  },
  clipTitle: {
    color: '#FFFFFF',
    fontSize: 18,
    fontWeight: '900',
    lineHeight: 22,
    marginBottom: 8,
  },
  creatorRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  creatorAvatar: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: 'rgba(255,255,255,0.1)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  creatorAvatarText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '800',
  },
  creatorName: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: 13,
    fontWeight: '600',
  },
  rightActionsColumn: {
    position: 'absolute',
    right: 16,
    bottom: 24,
    alignItems: 'center',
    gap: 18,
  },
  actionBtn: {
    alignItems: 'center',
    gap: 4,
  },
  actionIconCircle: {
    width: 46,
    height: 46,
    borderRadius: 23,
    backgroundColor: 'rgba(255,255,255,0.08)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionLikedCircle: {
    backgroundColor: 'rgba(255,46,99,0.15)',
    borderColor: 'rgba(255,46,99,0.5)',
  },
  actionSkipCircle: {
    backgroundColor: 'rgba(255,99,33,0.1)',
    borderColor: 'rgba(255,99,33,0.3)',
  },
  actionCountText: {
    color: 'rgba(255,255,255,0.7)',
    fontSize: 11,
    fontWeight: '700',
  },
  actionSkipText: {
    color: '#FF6321',
    fontSize: 10,
    fontWeight: '800',
  },
});
