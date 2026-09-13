import React, { useState } from 'react';
import {
  Modal,
  View,
  Text,
  TextInput,
  TouchableOpacity,
  Share as NativeShare,
  StyleSheet,
  Alert,
} from 'react-native';
import { X, Send, Share2, Copy, Check } from 'lucide-react-native';
import { FeedClip } from '../types';
import { shareAPI } from '../services/api';

interface ShareModalProps {
  visible: boolean;
  clip: FeedClip | null;
  onClose: () => void;
}

export const ShareModal: React.FC<ShareModalProps> = ({ visible, clip, onClose }) => {
  const [recipient, setRecipient] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!clip) return null;

  const handleSendDirect = async () => {
    if (!recipient.trim() || isSending) return;
    setIsSending(true);
    try {
      await shareAPI.sendShare(clip.id, recipient.trim());
      Alert.alert('Audio Shared!', `Sent "${clip.title}" directly to @${recipient.trim()}`);
      setRecipient('');
      onClose();
    } catch (err: any) {
      Alert.alert('Share Failed', err.message || 'Could not send audio clip.');
    } finally {
      setIsSending(false);
    }
  };

  const handleNativeShare = async () => {
    try {
      await NativeShare.share({
        message: `Listen to "${clip.title}" by @${clip.creator_name} on EchoFlow:\n${clip.hls_playlist_url || 'https://echoflow.audio'}`,
        title: clip.title,
      });
      onClose();
    } catch {
      // Ignored
    }
  };

  return (
    <Modal visible={visible} animationType="fade" transparent onRequestClose={onClose}>
      <View style={styles.overlay}>
        <TouchableOpacity style={styles.backdrop} onPress={onClose} activeOpacity={1} />

        <View style={styles.card}>
          <View style={styles.header}>
            <Text style={styles.title}>Share Audio Clip</Text>
            <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
              <X size={18} color="#FFFFFF" />
            </TouchableOpacity>
          </View>

          <View style={styles.clipPreview}>
            <View style={styles.previewBadge}>
              <Text style={styles.categoryText}>{clip.category}</Text>
            </View>
            <Text style={styles.previewTitle} numberOfLines={1}>
              {clip.title}
            </Text>
            <Text style={styles.previewCreator}>by @{clip.creator_name}</Text>
          </View>

          {/* In-app Share to username */}
          <Text style={styles.sectionLabel}>SEND TO ECHOFLOW USER</Text>
          <View style={styles.inputRow}>
            <TextInput
              style={styles.input}
              placeholder="Recipient username (e.g. alex)"
              placeholderTextColor="rgba(255,255,255,0.3)"
              value={recipient}
              onChangeText={setRecipient}
              autoCapitalize="none"
            />
            <TouchableOpacity
              style={[styles.sendBtn, (!recipient.trim() || isSending) && styles.disabledBtn]}
              onPress={handleSendDirect}
              disabled={!recipient.trim() || isSending}
            >
              <Send size={16} color="#000000" />
            </TouchableOpacity>
          </View>

          {/* Quick System Share */}
          <TouchableOpacity style={styles.nativeShareBtn} onPress={handleNativeShare}>
            <Share2 size={16} color="#FF6321" />
            <Text style={styles.nativeShareText}>Share via Apps or Socials</Text>
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.75)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
  },
  card: {
    width: '100%',
    maxWidth: 380,
    backgroundColor: '#141414',
    borderRadius: 24,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
    padding: 20,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  title: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '800',
    textTransform: 'uppercase',
  },
  closeBtn: {
    padding: 4,
    borderRadius: 16,
    backgroundColor: 'rgba(255,255,255,0.08)',
  },
  clipPreview: {
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderRadius: 16,
    padding: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    marginBottom: 16,
  },
  previewBadge: {
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(255,99,33,0.15)',
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 2,
    marginBottom: 6,
  },
  categoryText: {
    color: '#FF6321',
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  previewTitle: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '700',
  },
  previewCreator: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 11,
    marginTop: 2,
  },
  sectionLabel: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1,
    marginBottom: 8,
  },
  inputRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 14,
  },
  input: {
    flex: 1,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 10,
    color: '#FFFFFF',
    fontSize: 13,
  },
  sendBtn: {
    backgroundColor: '#FF6321',
    borderRadius: 12,
    paddingHorizontal: 16,
    justifyContent: 'center',
    alignItems: 'center',
  },
  disabledBtn: {
    opacity: 0.4,
  },
  nativeShareBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: 'rgba(255,99,33,0.08)',
    borderWidth: 1,
    borderColor: 'rgba(255,99,33,0.3)',
    borderRadius: 12,
    paddingVertical: 12,
  },
  nativeShareText: {
    color: '#FF6321',
    fontSize: 13,
    fontWeight: '700',
  },
});
