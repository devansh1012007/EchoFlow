import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { Audio } from 'expo-av';
import { Mic, Square, UploadCloud, CheckCircle2, ShieldCheck } from 'lucide-react-native';
import * as Haptics from 'expo-haptics';
import { uploadAPI } from '../services/api';

const CATEGORIES = [
  'Field Recordings',
  'Ambient & Drone',
  'Synthesizer',
  'Cyberpunk',
  'Lo-Fi Beats',
  'Speech & Poetry',
];

export const UploadScreen = ({ navigation }: any) => {
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState('Field Recordings');
  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [recordingUri, setRecordingUri] = useState<string | null>(null);
  const [recordingDuration, setRecordingDuration] = useState<number>(0);
  const [isRecording, setIsRecording] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [consentAccepted, setConsentAccepted] = useState(true);

  useEffect(() => {
    return () => {
      if (recording) {
        recording.stopAndUnloadAsync();
      }
    };
  }, [recording]);

  const startRecording = async () => {
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
      const permission = await Audio.requestPermissionsAsync();
      if (!permission.granted) {
        Alert.alert('Permission Denied', 'Microphone permission is required to record audio clips.');
        return;
      }

      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      });

      const { recording: newRecording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY
      );

      setRecording(newRecording);
      setIsRecording(true);
      setRecordingDuration(0);

      newRecording.setOnRecordingStatusUpdate((status) => {
        if (status.isRecording) {
          setRecordingDuration(Math.floor(status.durationMillis / 1000));
        }
      });
    } catch (err: any) {
      Alert.alert('Recording Failed', err.message || 'Could not start audio recorder.');
    }
  };

  const stopRecording = async () => {
    if (!recording) return;
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      await recording.stopAndUnloadAsync();
      const uri = recording.getURI();
      setRecordingUri(uri);
      setRecording(null);
      setIsRecording(false);
    } catch (err: any) {
      Alert.alert('Error', 'Could not stop recording.');
    }
  };

  const handlePublish = async () => {
    if (!title.trim()) {
      Alert.alert('Title Required', 'Please enter a descriptive title for your audio reel.');
      return;
    }

    if (!consentAccepted) {
      Alert.alert('Compliance Required', 'Please accept the content guidelines and licensing confirmation.');
      return;
    }

    setIsUploading(true);
    try {
      const formData = new FormData();
      formData.append('title', title.trim());
      formData.append('category', category);

      if (recordingUri) {
        const filename = recordingUri.split('/').pop() || 'recording.m4a';
        // Append audio file for React Native
        formData.append('audio_file', {
          uri: recordingUri,
          name: filename,
          type: 'audio/m4a',
        } as any);
      }

      await uploadAPI.uploadAudio(formData);
      Alert.alert(
        'Upload Submitted!',
        'Your audio clip is undergoing AI safety moderation and HLS slicing. It will appear on the feed shortly.',
        [{ text: 'Go to Feed', onPress: () => navigation.navigate('Feed') }]
      );
      setTitle('');
      setRecordingUri(null);
      setRecordingDuration(0);
    } catch (err: any) {
      Alert.alert('Upload Error', err.message || 'Could not upload audio reel.');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <UploadCloud size={24} color="#FF6321" />
        <Text style={styles.title}>CREATOR STUDIO</Text>
        <Text style={styles.subtitle}>Capture an audio clip or field sound to share with listeners</Text>
      </View>

      {/* Record Mic Section */}
      <View style={styles.recordBox}>
        <Text style={styles.recordBoxTitle}>
          {isRecording ? 'RECORDING IN PROGRESS' : recordingUri ? 'AUDIO CAPTURED' : 'TAP TO RECORD'}
        </Text>

        <TouchableOpacity
          style={[
            styles.micBtn,
            isRecording && styles.micBtnActive,
            !!recordingUri && !isRecording && styles.micBtnDone,
          ]}
          onPress={isRecording ? stopRecording : startRecording}
          activeOpacity={0.8}
        >
          {isRecording ? (
            <Square size={28} color="#FFFFFF" fill="#FFFFFF" />
          ) : recordingUri ? (
            <CheckCircle2 size={32} color="#10B981" />
          ) : (
            <Mic size={32} color="#000000" />
          )}
        </TouchableOpacity>

        <Text style={styles.timerText}>
          {isRecording
            ? `00:${recordingDuration < 10 ? '0' : ''}${recordingDuration}`
            : recordingUri
            ? `Captured ${recordingDuration}s Audio File`
            : 'Max clip duration: 60 seconds'}
        </Text>

        {recordingUri && !isRecording && (
          <TouchableOpacity onPress={() => setRecordingUri(null)} style={styles.reRecordBtn}>
            <Text style={styles.reRecordText}>Re-record Clip</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Form Fields */}
      <View style={styles.fieldGroup}>
        <Text style={styles.label}>CLIP TITLE</Text>
        <TextInput
          style={styles.input}
          placeholder="e.g. Midnight Cyberpunk Rain in Tokyo"
          placeholderTextColor="rgba(255,255,255,0.3)"
          value={title}
          onChangeText={setTitle}
        />
      </View>

      <View style={styles.fieldGroup}>
        <Text style={styles.label}>CATEGORY</Text>
        <View style={styles.categoryGrid}>
          {CATEGORIES.map((cat) => {
            const isSel = category === cat;
            return (
              <TouchableOpacity
                key={cat}
                onPress={() => setCategory(cat)}
                style={[styles.categoryOption, isSel && styles.categoryOptionActive]}
              >
                <Text style={[styles.categoryOptionText, isSel && styles.categoryOptionTextActive]}>
                  {cat}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>
      </View>

      {/* Compliance / DPDP Confirmation */}
      <TouchableOpacity
        style={styles.complianceRow}
        onPress={() => setConsentAccepted(!consentAccepted)}
        activeOpacity={0.8}
      >
        <View style={[styles.checkbox, consentAccepted && styles.checkboxChecked]}>
          {consentAccepted && <CheckCircle2 size={14} color="#000000" />}
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.complianceTitle}>Content Guidelines & Licensing</Text>
          <Text style={styles.complianceSub}>
            I confirm this audio is my original creation or licensed under CC/Public Domain, and complies with EchoFlow safety policies.
          </Text>
        </View>
      </TouchableOpacity>

      {/* Publish Button */}
      <TouchableOpacity
        style={[styles.publishBtn, isUploading && styles.publishBtnDisabled]}
        onPress={handlePublish}
        disabled={isUploading}
      >
        {isUploading ? (
          <ActivityIndicator color="#000000" />
        ) : (
          <Text style={styles.publishBtnText}>PUBLISH TO FEED</Text>
        )}
      </TouchableOpacity>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0A0A',
  },
  content: {
    padding: 20,
    paddingBottom: 40,
  },
  header: {
    marginBottom: 20,
  },
  title: {
    color: '#FFFFFF',
    fontSize: 18,
    fontWeight: '900',
    marginTop: 6,
  },
  subtitle: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 12,
    marginTop: 4,
  },
  recordBox: {
    backgroundColor: '#141414',
    borderRadius: 20,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    padding: 24,
    alignItems: 'center',
    marginBottom: 24,
  },
  recordBoxTitle: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1,
    marginBottom: 16,
  },
  micBtn: {
    width: 76,
    height: 76,
    borderRadius: 38,
    backgroundColor: '#FF6321',
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#FF6321',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.35,
    shadowRadius: 15,
    elevation: 8,
  },
  micBtnActive: {
    backgroundColor: '#EF4444',
  },
  micBtnDone: {
    backgroundColor: 'rgba(16,185,129,0.2)',
    borderWidth: 2,
    borderColor: '#10B981',
  },
  timerText: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '700',
    fontFamily: 'Courier',
    marginTop: 14,
  },
  reRecordBtn: {
    marginTop: 10,
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: 12,
    backgroundColor: 'rgba(255,255,255,0.08)',
  },
  reRecordText: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: 11,
    fontWeight: '700',
  },
  fieldGroup: {
    marginBottom: 20,
  },
  label: {
    color: 'rgba(255,255,255,0.5)',
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1,
    marginBottom: 8,
  },
  input: {
    backgroundColor: '#141414',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.1)',
    borderRadius: 14,
    paddingHorizontal: 16,
    paddingVertical: 12,
    color: '#FFFFFF',
    fontSize: 14,
  },
  categoryGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  categoryOption: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 10,
    backgroundColor: 'rgba(255,255,255,0.05)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.1)',
  },
  categoryOptionActive: {
    backgroundColor: '#FF6321',
    borderColor: '#FF6321',
  },
  categoryOptionText: {
    color: 'rgba(255,255,255,0.7)',
    fontSize: 11,
    fontWeight: '700',
  },
  categoryOptionTextActive: {
    color: '#000000',
    fontWeight: '900',
  },
  complianceRow: {
    flexDirection: 'row',
    gap: 12,
    backgroundColor: 'rgba(255,255,255,0.03)',
    borderRadius: 14,
    padding: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.06)',
    marginBottom: 24,
  },
  checkbox: {
    width: 20,
    height: 20,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.3)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  checkboxChecked: {
    backgroundColor: '#FF6321',
    borderColor: '#FF6321',
  },
  complianceTitle: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '800',
  },
  complianceSub: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 10,
    marginTop: 2,
    lineHeight: 14,
  },
  publishBtn: {
    backgroundColor: '#FF6321',
    borderRadius: 14,
    paddingVertical: 16,
    alignItems: 'center',
    shadowColor: '#FF6321',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 10,
    elevation: 6,
  },
  publishBtnDisabled: {
    opacity: 0.5,
  },
  publishBtnText: {
    color: '#000000',
    fontSize: 14,
    fontWeight: '900',
    letterSpacing: 1,
  },
});
