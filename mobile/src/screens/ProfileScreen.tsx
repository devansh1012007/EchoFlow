import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  FlatList,
  StyleSheet,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { User, LogOut, Heart, Music, Settings, Shield } from 'lucide-react-native';
import { OwnProfile, PublicProfile, FeedClip } from '../types';
import { profileAPI } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { usePlayer } from '../context/PlayerContext';

export const ProfileScreen = ({ route, navigation }: any) => {
  const targetUserId = route.params?.userId;
  const { user, logout } = useAuth();
  const { playClip } = usePlayer();

  const [ownProfile, setOwnProfile] = useState<OwnProfile | null>(null);
  const [publicProfile, setPublicProfile] = useState<PublicProfile | null>(null);
  const [activeTab, setActiveTab] = useState<'uploads' | 'liked'>('liked');
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const isOwn = !targetUserId || targetUserId === user?.id;

  useEffect(() => {
    loadProfile();
  }, [targetUserId]);

  const loadProfile = async () => {
    setIsLoading(true);
    try {
      if (isOwn) {
        const data = await profileAPI.getOwnProfile();
        setOwnProfile(data);
      } else {
        const data = await profileAPI.getPublicProfile(targetUserId);
        setPublicProfile(data);
      }
    } catch {
      // Fallback
    } finally {
      setIsLoading(false);
    }
  };

  const handleLogout = () => {
    Alert.alert('Sign Out', 'Are you sure you want to sign out of EchoFlow?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign Out',
        style: 'destructive',
        onPress: async () => {
          await logout();
          navigation.navigate('Feed');
        },
      },
    ]);
  };

  const currentData = isOwn ? ownProfile : publicProfile;
  const username = currentData?.username || user?.username || 'SonicCreator';
  const likedClips = ownProfile?.liked_clips || [];

  return (
    <View style={styles.container}>
      {/* Profile Header */}
      <View style={styles.profileHeader}>
        <View style={styles.avatarLarge}>
          <Text style={styles.avatarLetter}>{username.slice(0, 1).toUpperCase()}</Text>
        </View>

        <Text style={styles.profileUsername}>@{username}</Text>
        <Text style={styles.profileRole}>ECHOFLOW AUDIO CITIZEN</Text>

        {/* Stats Row */}
        <View style={styles.statsRow}>
          <View style={styles.statBox}>
            <Text style={styles.statValue}>{currentData?.uploads_count || 0}</Text>
            <Text style={styles.statLabel}>UPLOADS</Text>
          </View>
          <View style={styles.statDivider} />
          <View style={styles.statBox}>
            <Text style={styles.statValue}>{currentData?.followers_count || 0}</Text>
            <Text style={styles.statLabel}>FOLLOWERS</Text>
          </View>
          <View style={styles.statDivider} />
          <View style={styles.statBox}>
            <Text style={styles.statValue}>{currentData?.following_count || 0}</Text>
            <Text style={styles.statLabel}>FOLLOWING</Text>
          </View>
        </View>

        {/* Sign Out Button if own profile */}
        {isOwn && (
          <TouchableOpacity style={styles.logoutBtn} onPress={handleLogout}>
            <LogOut size={14} color="#EF4444" />
            <Text style={styles.logoutText}>Sign Out</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Tabs */}
      <View style={styles.tabBar}>
        <TouchableOpacity
          style={[styles.tabItem, activeTab === 'liked' && styles.tabItemActive]}
          onPress={() => setActiveTab('liked')}
        >
          <Heart size={16} color={activeTab === 'liked' ? '#FF6321' : 'rgba(255,255,255,0.4)'} />
          <Text style={[styles.tabLabel, activeTab === 'liked' && styles.tabLabelActive]}>
            Liked Clips ({likedClips.length})
          </Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.tabItem, activeTab === 'uploads' && styles.tabItemActive]}
          onPress={() => setActiveTab('uploads')}
        >
          <Music size={16} color={activeTab === 'uploads' ? '#FF6321' : 'rgba(255,255,255,0.4)'} />
          <Text style={[styles.tabLabel, activeTab === 'uploads' && styles.tabLabelActive]}>
            Uploads ({currentData?.uploads_count || 0})
          </Text>
        </TouchableOpacity>
      </View>

      {/* Content List */}
      {isLoading ? (
        <View style={styles.centerBox}>
          <ActivityIndicator color="#FF6321" />
        </View>
      ) : activeTab === 'liked' && likedClips.length === 0 ? (
        <View style={styles.centerBox}>
          <Heart size={36} color="rgba(255,255,255,0.2)" />
          <Text style={styles.emptyTitle}>No liked audio clips yet</Text>
          <Text style={styles.emptySub}>Double tap or press the heart on the feed to save tracks here.</Text>
        </View>
      ) : activeTab === 'liked' ? (
        <FlatList
          data={likedClips}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.listContainer}
          renderItem={({ item }) => (
            <TouchableOpacity
              style={styles.clipRow}
              onPress={() => {
                playClip(item);
                navigation.navigate('Feed');
              }}
            >
              <View style={styles.clipIcon}>
                <Music size={18} color="#000000" />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.clipTitle} numberOfLines={1}>
                  {item.title}
                </Text>
                <Text style={styles.clipSub}>
                  @{item.creator_name} · {item.category}
                </Text>
              </View>
              <Text style={styles.likesCount}>❤️ {item.likes || 0}</Text>
            </TouchableOpacity>
          )}
        />
      ) : (
        <View style={styles.centerBox}>
          <Music size={36} color="rgba(255,255,255,0.2)" />
          <Text style={styles.emptyTitle}>Creator Catalog</Text>
          <Text style={styles.emptySub}>All approved audio reels uploaded to EchoFlow.</Text>
        </View>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0A0A',
  },
  profileHeader: {
    alignItems: 'center',
    paddingVertical: 24,
    paddingHorizontal: 20,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  avatarLarge: {
    width: 76,
    height: 76,
    borderRadius: 38,
    backgroundColor: '#FF6321',
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#FF6321',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.35,
    shadowRadius: 12,
    elevation: 6,
    marginBottom: 12,
  },
  avatarLetter: {
    color: '#000000',
    fontSize: 32,
    fontWeight: '900',
  },
  profileUsername: {
    color: '#FFFFFF',
    fontSize: 20,
    fontWeight: '900',
  },
  profileRole: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1,
    marginTop: 3,
  },
  statsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 20,
    backgroundColor: '#141414',
    borderRadius: 16,
    paddingVertical: 12,
    paddingHorizontal: 20,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
  },
  statBox: {
    alignItems: 'center',
    minWidth: 70,
  },
  statValue: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '900',
  },
  statLabel: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 9,
    fontWeight: '800',
    marginTop: 2,
    letterSpacing: 0.5,
  },
  statDivider: {
    width: 1,
    height: 24,
    backgroundColor: 'rgba(255,255,255,0.1)',
  },
  logoutBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 16,
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 12,
    backgroundColor: 'rgba(239,68,68,0.1)',
    borderWidth: 1,
    borderColor: 'rgba(239,68,68,0.3)',
  },
  logoutText: {
    color: '#EF4444',
    fontSize: 11,
    fontWeight: '700',
  },
  tabBar: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.08)',
  },
  tabItem: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 14,
  },
  tabItemActive: {
    borderBottomWidth: 2,
    borderBottomColor: '#FF6321',
  },
  tabLabel: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 12,
    fontWeight: '800',
  },
  tabLabelActive: {
    color: '#FF6321',
  },
  centerBox: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 30,
  },
  emptyTitle: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '800',
    marginTop: 12,
  },
  emptySub: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 12,
    marginTop: 4,
    textAlign: 'center',
  },
  listContainer: {
    padding: 16,
    gap: 10,
  },
  clipRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#141414',
    borderRadius: 14,
    padding: 12,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.06)',
    gap: 12,
  },
  clipIcon: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: '#FF6321',
    justifyContent: 'center',
    alignItems: 'center',
  },
  clipTitle: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '800',
  },
  clipSub: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 11,
    marginTop: 2,
  },
  likesCount: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: 11,
    fontWeight: '700',
  },
});
