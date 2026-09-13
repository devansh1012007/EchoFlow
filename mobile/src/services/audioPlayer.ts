import { Audio, AVPlaybackStatus } from 'expo-av';
import { FeedClip } from '../types';
import { interactionsAPI } from './api';

class MobileAudioPlayer {
  private sound: Audio.Sound | null = null;
  private currentClip: FeedClip | null = null;
  private isLoaded: boolean = false;
  private startTimeMs: number = 0;
  private onPlaybackStatusUpdateCallback: ((status: AVPlaybackStatus) => void) | null = null;
  private onTrackFinishedCallback: (() => void) | null = null;

  constructor() {
    this.initAudioMode();
  }

  private async initAudioMode() {
    try {
      await Audio.setAudioModeAsync({
        allowsRecordingIOS: false,
        playsInSilentModeIOS: true,
        staysActiveInBackground: true,
        shouldDuckAndroid: true,
        playThroughEarpieceAndroid: false,
      });
    } catch {
      // Audio mode init
    }
  }

  public setStatusCallback(cb: ((status: AVPlaybackStatus) => void) | null) {
    this.onPlaybackStatusUpdateCallback = cb;
  }

  public setTrackFinishedCallback(cb: (() => void) | null) {
    this.onTrackFinishedCallback = cb;
  }

  public async loadAndPlay(clip: FeedClip): Promise<void> {
    // Flush telemetry of previous track if played
    await this.flushTelemetry();

    await this.unload();
    this.currentClip = clip;
    this.startTimeMs = Date.now();

    const audioUrl = clip.hls_playlist_url;
    if (!audioUrl) {
      return;
    }

    try {
      const { sound } = await Audio.Sound.createAsync(
        { uri: audioUrl },
        { shouldPlay: true, progressUpdateIntervalMillis: 250 },
        this.handlePlaybackStatusUpdate
      );
      this.sound = sound;
      this.isLoaded = true;
    } catch (err) {
      console.warn('Failed to load audio track:', err);
    }
  }

  private handlePlaybackStatusUpdate = (status: AVPlaybackStatus) => {
    if (this.onPlaybackStatusUpdateCallback) {
      this.onPlaybackStatusUpdateCallback(status);
    }

    if (status.isLoaded) {
      if (status.didJustFinish && !status.isLooping) {
        if (this.onTrackFinishedCallback) {
          this.onTrackFinishedCallback();
        }
      }
    }
  };

  public async play(): Promise<void> {
    if (this.sound && this.isLoaded) {
      await this.sound.playAsync();
    }
  }

  public async pause(): Promise<void> {
    if (this.sound && this.isLoaded) {
      await this.sound.pauseAsync();
    }
  }

  public async togglePlayPause(): Promise<boolean> {
    if (!this.sound || !this.isLoaded) return false;
    const status = await this.sound.getStatusAsync();
    if (status.isLoaded) {
      if (status.isPlaying) {
        await this.sound.pauseAsync();
        return false;
      } else {
        await this.sound.playAsync();
        return true;
      }
    }
    return false;
  }

  public async seek(positionMillis: number): Promise<void> {
    if (this.sound && this.isLoaded) {
      await this.sound.setPositionAsync(positionMillis);
    }
  }

  public async flushTelemetry(): Promise<void> {
    if (this.currentClip && this.startTimeMs > 0) {
      const durationSec = Math.round((Date.now() - this.startTimeMs) / 1000);
      const clipId = this.currentClip.id;
      this.startTimeMs = 0;
      if (durationSec > 1) {
        try {
          await interactionsAPI.logTelemetry(clipId, durationSec);
        } catch {
          // Telemetry fire & forget
        }
      }
    }
  }

  public async unload(): Promise<void> {
    if (this.sound) {
      try {
        await this.sound.stopAsync();
        await this.sound.unloadAsync();
      } catch {
        // Unload errors safe to ignore
      }
      this.sound = null;
      this.isLoaded = false;
    }
  }
}

export const mobileAudioPlayer = new MobileAudioPlayer();
