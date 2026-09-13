import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { Play, Pause, Sparkles, Radio, Compass } from 'lucide-react-native';
import { FeedClip } from '../types';
import { feedAPI } from '../services/api';
import { usePlayer } from '../context/PlayerContext';

const CATEGORIES = [
  'All Tracks',
  'Ambient & Drone',
  'Field Recordings',
  'Synthesizer',
  'Cyberpunk',
  'Lo-Fi Beats',
  'Speech & Poetry',
];

export const ExploreScreen = ({ navigation }: any) => {
  const [selectedCategory, setSelectedCategory] = useState<string>('All Tracks');
  const [clips, setClips] = useState<FeedClip[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const { currentClip, isPlaying, playClip, togglePlayPause } = usePlayer();

  useEffect(() => {
    loadCategory(selectedCategory);
  }, [selectedCategory]);

  const loadCategory = async (cat: string) => {
    setIsLoading(true);
    try {
      const filterParam = cat === 'All Tracks' ? undefined : cat;
      const res = await feedAPI.getSuggestions(filterParam);
      setClips(res.results || []);
    } catch {
      setClips([]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCardPress = (clip: FeedClip) => {
    if (currentClip?.id === clip.id) {
      togglePlayPause();
    } else {
      playClip(clip);
    }
  };

  return (
    <View style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerTitleRow}>
          <Compass size={20} color="#FF6321" />
          <Text style={styles.title}>VECTOR DISCOVERY</Text>
        </View>
        <Text style={styles.subtitle}>Explore soundscapes tuned by semantic embeddings</Text>
      </View>

      {/* Categories Horizontal Scroll */}
      <View style={styles.categoryContainer}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.categoryList}>
          {CATEGORIES.map((cat) => {
            const isSelected = selectedCategory === cat;
            return (
              <TouchableOpacity
                key={cat}
                onPress={() => setSelectedCategory(cat)}
                style={[styles.categoryPill, isSelected && styles.categoryPillActive]}
              >
                <Text style={[styles.categoryText, isSelected && styles.categoryTextActive]}>
                  {cat}
                </Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      </View>

      {/* List */}
      {isLoading ? (
        <View style={styles.centerContainer}>
          <ActivityIndicator size="small" color="#FF6321" />
          <Text style={styles.loadingText}>Computing similarity rankings...</Text>
        </View>
      ) : clips.length === 0 ? (
        <View style={styles.centerContainer}>
          <Radio size={36} color="rgba(255,255,255,0.2)" />
          <Text style={styles.emptyTitle}>No audio tracks found</Text>
          <Text style={styles.emptySub}>Try exploring a different category or upload a sound</Text>
        </View>
      ) : (
        <FlatList
          data={clips}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.listContent}
          renderItem={({ item }) => {
            const isCurrent = currentClip?.id === item.id;
            return (
              <TouchableOpacity
                style={[styles.clipCard, isCurrent && styles.clipCardActive]}
                onPress={() => handleCardPress(item)}
                activeOpacity={0.8}
              >
                <View style={styles.playIconBox}>
                  {isCurrent && isPlaying ? (
                    <Pause size={18} color="#000000" />
                  ) : (
                    <Play size={18} color="#000000" style={{ marginLeft: 2 }} />
                  )}
                </View>

                <View style={styles.cardInfo}>
                  <View style={styles.cardCategoryBadge}>
                    <Text style={styles.cardCategoryText}>{item.category}</Text>
                  </View>
                  <Text style={styles.cardTitle} numberOfLines={1}>
                    {item.title}
                  </Text>
                  <Text style={styles.cardCreator}>@{item.creator_name}</Text>
                </View>

                <View style={styles.cardStats}>
                  <Text style={styles.likesText}>❤️ {item.likes || 0}</Text>
                  <Text style={styles.commentsText}>💬 {item.comment_count || 0}</Text>
                </View>
              </TouchableOpacity>
            );
          }}
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
  },
  headerTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  title: {
    color: '#F5F5F5',
    fontSize: 18,
    fontWeight: '900',
    letterSpacing: 0.5,
  },
  subtitle: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 12,
    marginTop: 4,
  },
  categoryContainer: {
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.06)',
    paddingBottom: 12,
  },
  categoryList: {
    paddingHorizontal: 20,
    gap: 8,
  },
  categoryPill: {
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.1)',
  },
  categoryPillActive: {
    backgroundColor: '#FF6321',
    borderColor: '#FF6321',
  },
  categoryText: {
    color: 'rgba(255,255,255,0.7)',
    fontSize: 12,
    fontWeight: '700',
  },
  categoryTextActive: {
    color: '#000000',
    fontWeight: '900',
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
    marginTop: 12,
  },
  emptySub: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 12,
    marginTop: 4,
  },
  listContent: {
    padding: 20,
    gap: 12,
  },
  clipCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#141414',
    borderRadius: 16,
    padding: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    gap: 14,
  },
  clipCardActive: {
    borderColor: '#FF6321',
    backgroundColor: '#1A1512',
  },
  playIconBox: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#FF6321',
    justifyContent: 'center',
    alignItems: 'center',
  },
  cardInfo: {
    flex: 1,
  },
  cardCategoryBadge: {
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(255,99,33,0.12)',
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
    marginBottom: 4,
  },
  cardCategoryText: {
    color: '#FF6321',
    fontSize: 9,
    fontWeight: '800',
    textTransform: 'uppercase',
  },
  cardTitle: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '800',
  },
  cardCreator: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 11,
    marginTop: 2,
  },
  cardStats: {
    alignItems: 'flex-end',
    gap: 4,
  },
  likesText: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: 11,
    fontWeight: '700',
  },
  commentsText: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 11,
  },
});
