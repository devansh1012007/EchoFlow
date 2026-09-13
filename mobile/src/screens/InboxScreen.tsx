import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  RefreshControl,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { Inbox, Play, UserCheck, MessageSquareShare } from 'lucide-react-native';
import { ShareEvent } from '../types';
import { shareAPI } from '../services/api';
import { usePlayer } from '../context/PlayerContext';

export const InboxScreen = ({ navigation }: any) => {
  const [shares, setShares] = useState<ShareEvent[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);

  const { playClip } = usePlayer();

  useEffect(() => {
    loadInbox();
  }, []);

  const loadInbox = async () => {
    try {
      const res = await shareAPI.getInbox();
      setShares(res.results || []);
    } catch {
      setShares([]);
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  const handlePlaySharedClip = (event: ShareEvent) => {
    if (event.clip) {
      playClip(event.clip);
      navigation.navigate('Feed');
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View style={styles.titleRow}>
          <Inbox size={20} color="#FF6321" />
          <Text style={styles.title}>INBOX & SHARES</Text>
        </View>
        <Text style={styles.subtitle}>Audio clips shared with you by friends and creators</Text>
      </View>

      {isLoading ? (
        <View style={styles.centerContainer}>
          <ActivityIndicator size="small" color="#FF6321" />
          <Text style={styles.loadingText}>Fetching shared audio clips...</Text>
        </View>
      ) : shares.length === 0 ? (
        <View style={styles.centerContainer}>
          <MessageSquareShare size={40} color="rgba(255,255,255,0.2)" />
          <Text style={styles.emptyTitle}>Your inbox is quiet</Text>
          <Text style={styles.emptySub}>
            When another user sends an audio reel to your handle, it will appear here.
          </Text>
        </View>
      ) : (
        <FlatList
          data={shares}
          keyExtractor={(item) => item.id.toString()}
          contentContainerStyle={styles.listContent}
          refreshControl={
            <RefreshControl
              refreshing={isRefreshing}
              onRefresh={() => {
                setIsRefreshing(true);
                loadInbox();
              }}
              tintColor="#FF6321"
            />
          }
          renderItem={({ item }) => (
            <TouchableOpacity
              style={styles.shareCard}
              onPress={() => handlePlaySharedClip(item)}
              activeOpacity={0.8}
            >
              <View style={styles.senderAvatar}>
                <Text style={styles.avatarLetter}>
                  {item.sender_name?.slice(0, 1).toUpperCase() || 'U'}
                </Text>
              </View>

              <View style={styles.shareInfo}>
                <Text style={styles.senderName}>@{item.sender_name} shared an audio track</Text>
                <Text style={styles.clipTitle} numberOfLines={1}>
                  {item.clip_title || item.clip?.title || 'Shared Audio Clip'}
                </Text>
                <Text style={styles.timeAgo}>
                  {new Date(item.created_at).toLocaleDateString()}
                </Text>
              </View>

              <View style={styles.playBtn}>
                <Play size={16} color="#000000" style={{ marginLeft: 2 }} />
              </View>
            </TouchableOpacity>
          )}
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
  header: {
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  titleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  title: {
    color: '#FFFFFF',
    fontSize: 18,
    fontWeight: '900',
    letterSpacing: 0.5,
  },
  subtitle: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 12,
    marginTop: 4,
  },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  loadingText: {
    color: 'rgba(255,255,255,0.5)',
    fontSize: 12,
    marginTop: 10,
  },
  emptyTitle: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '800',
    marginTop: 14,
  },
  emptySub: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 12,
    marginTop: 4,
    textAlign: 'center',
    maxWidth: 280,
  },
  listContent: {
    padding: 20,
    gap: 12,
  },
  shareCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#141414',
    borderRadius: 16,
    padding: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    gap: 14,
  },
  senderAvatar: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: 'rgba(255,99,33,0.15)',
    borderWidth: 1,
    borderColor: 'rgba(255,99,33,0.3)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  avatarLetter: {
    color: '#FF6321',
    fontWeight: '900',
    fontSize: 16,
  },
  shareInfo: {
    flex: 1,
  },
  senderName: {
    color: '#FF6321',
    fontSize: 11,
    fontWeight: '700',
  },
  clipTitle: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '800',
    marginTop: 2,
  },
  timeAgo: {
    color: 'rgba(255,255,255,0.3)',
    fontSize: 10,
    marginTop: 2,
  },
  playBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: '#FF6321',
    justifyContent: 'center',
    alignItems: 'center',
  },
});
