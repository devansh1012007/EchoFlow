import React, { useState, useEffect } from 'react';
import {
  Modal,
  View,
  Text,
  TextInput,
  TouchableOpacity,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
  StyleSheet,
} from 'react-native';
import { X, Send, MessageSquare } from 'lucide-react-native';
import { Comment } from '../types';
import { commentsAPI } from '../services/api';

interface CommentModalProps {
  visible: boolean;
  clipId: string;
  clipTitle: string;
  onClose: () => void;
}

export const CommentModal: React.FC<CommentModalProps> = ({
  visible,
  clipId,
  clipTitle,
  onClose,
}) => {
  const [comments, setComments] = useState<Comment[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [newText, setNewText] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  useEffect(() => {
    if (visible && clipId) {
      loadComments();
    }
  }, [visible, clipId]);

  const loadComments = async () => {
    setIsLoading(true);
    try {
      const res = await commentsAPI.getComments(clipId);
      setComments(res.results || []);
    } catch {
      setComments([]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmit = async () => {
    if (!newText.trim() || isSubmitting) return;
    setIsSubmitting(true);
    try {
      const created = await commentsAPI.postComment(clipId, newText.trim());
      setComments((prev) => [created, ...prev]);
      setNewText('');
    } catch (err) {
      console.warn('Failed to post comment', err);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <KeyboardAvoidingView
        style={styles.overlay}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <TouchableOpacity style={styles.backdrop} onPress={onClose} activeOpacity={1} />

        <View style={styles.sheetContainer}>
          {/* Header */}
          <View style={styles.header}>
            <View style={styles.indicator} />
            <View style={styles.titleRow}>
              <View>
                <Text style={styles.title}>Comments</Text>
                <Text style={styles.subtitle} numberOfLines={1}>
                  {clipTitle}
                </Text>
              </View>
              <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
                <X size={20} color="#FFFFFF" />
              </TouchableOpacity>
            </View>
          </View>

          {/* List */}
          {isLoading ? (
            <View style={styles.centerContainer}>
              <ActivityIndicator color="#FF6321" size="small" />
              <Text style={styles.loadingText}>Fetching comments...</Text>
            </View>
          ) : comments.length === 0 ? (
            <View style={styles.centerContainer}>
              <MessageSquare size={32} color="rgba(255,255,255,0.2)" />
              <Text style={styles.emptyText}>No thoughts shared yet.</Text>
              <Text style={styles.emptySubtext}>Be the first to leave a voice in the thread.</Text>
            </View>
          ) : (
            <FlatList
              data={comments}
              keyExtractor={(item) => item.id}
              contentContainerStyle={styles.listContent}
              renderItem={({ item }) => (
                <View style={styles.commentItem}>
                  <View style={styles.avatar}>
                    <Text style={styles.avatarText}>
                      {item.author_username?.slice(0, 1).toUpperCase() || 'U'}
                    </Text>
                  </View>
                  <View style={styles.commentBody}>
                    <View style={styles.commentMeta}>
                      <Text style={styles.authorName}>@{item.author_username}</Text>
                      <Text style={styles.timeText}>
                        {new Date(item.created_at).toLocaleDateString()}
                      </Text>
                    </View>
                    <Text style={styles.commentText}>{item.text}</Text>
                  </View>
                </View>
              )}
            />
          )}

          {/* Input Bar */}
          <View style={styles.inputContainer}>
            <TextInput
              style={styles.textInput}
              placeholder="Add your comment..."
              placeholderTextColor="rgba(255,255,255,0.4)"
              value={newText}
              onChangeText={setNewText}
              multiline
              maxLength={280}
            />
            <TouchableOpacity
              style={[styles.sendBtn, !newText.trim() && styles.sendBtnDisabled]}
              onPress={handleSubmit}
              disabled={!newText.trim() || isSubmitting}
            >
              <Send size={18} color="#000000" />
            </TouchableOpacity>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    justifyContent: 'flex-end',
  },
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.65)',
  },
  sheetContainer: {
    backgroundColor: '#141414',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.1)',
    height: '65%',
    paddingBottom: 24,
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.08)',
  },
  indicator: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: 'rgba(255,255,255,0.2)',
    alignSelf: 'center',
    marginBottom: 10,
  },
  titleRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  title: {
    color: '#F5F5F5',
    fontSize: 16,
    fontWeight: '800',
    textTransform: 'uppercase',
  },
  subtitle: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 11,
    maxWidth: 240,
  },
  closeBtn: {
    padding: 6,
    borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.06)',
  },
  centerContainer: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  loadingText: {
    color: 'rgba(255,255,255,0.5)',
    fontSize: 12,
    marginTop: 8,
  },
  emptyText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '700',
    marginTop: 12,
  },
  emptySubtext: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 12,
    marginTop: 4,
    textAlign: 'center',
  },
  listContent: {
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  commentItem: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 16,
  },
  avatar: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: '#FF6321',
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarText: {
    color: '#000000',
    fontWeight: '900',
    fontSize: 13,
  },
  commentBody: {
    flex: 1,
  },
  commentMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 2,
  },
  authorName: {
    color: '#FF6321',
    fontSize: 12,
    fontWeight: '700',
  },
  timeText: {
    color: 'rgba(255,255,255,0.3)',
    fontSize: 10,
  },
  commentText: {
    color: 'rgba(255,255,255,0.85)',
    fontSize: 13,
    lineHeight: 18,
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: 'rgba(255,255,255,0.08)',
    gap: 10,
  },
  textInput: {
    flex: 1,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 10,
    color: '#FFFFFF',
    fontSize: 13,
    maxHeight: 90,
  },
  sendBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: '#FF6321',
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendBtnDisabled: {
    opacity: 0.4,
  },
});
