# EchoFlow Mobile — React Native (Expo)

**EchoFlow — TikTok for your ears** on iOS and Android. An audio-first, short-form mobile application built with **React Native**, **Expo SDK 52**, and **TypeScript**.

---

## 📱 Features

1. **Audio Feed & Vertical Reels**:
   - Vertical flick & swipe paging (`FlatList` with `pagingEnabled`).
   - Pull-to-refresh (`RefreshControl` with custom `#FF6321` indicator) to pull the latest vector stream from PostgreSQL / pgvector HNSW index.
   - Live pulsating audio equalizer visualizer bars.
   - Hands-free auto-advance mode (automatically plays next clip when track ends).
   - One-tap likes with haptic feedback (`expo-haptics`).

2. **Full Background Audio (`expo-av`)**:
   - `UIBackgroundModes: ["audio"]` configured in `app.json`.
   - Silent mode bypass on iOS and Android audio ducking.
   - Automatic telemetry recording to Django backend (`/interactions/{id}/log-telemetry/`).

3. **Creator Studio & Recording**:
   - Native microphone recording (`Audio.Recording`) with live timer.
   - Upload audio reels with metadata and category selection.
   - Regulatory and DPDP compliance confirmation.

4. **Vector Discovery**:
   - Category filtering pills (Ambient, Synthesizer, Field Recordings, Lo-Fi, Cyberpunk).
   - Real-time similarity rankings.

5. **Inbox & Social Sharing**:
   - Dedicated notification inbox for clips sent to your handle.
   - Native iOS/Android share sheet integration (`Share.share`) or direct handle share.

6. **Profile & Catalog**:
   - User stats: uploads, followers, following.
   - Liked audio reels tab with instant playback.

---

## 🚀 Getting Started

### 1. Prerequisites
- Node.js 18+ or 20+
- Expo CLI (`npx expo`)
- iOS Simulator (macOS / Xcode) OR Android Emulator (Android Studio) OR the **Expo Go** app on your physical iPhone or Android.

### 2. Installation
```bash
cd mobile
npm install
```

### 3. Configure Backend URL
Open `mobile/src/services/api.ts` and set `API_BASE_URL`:
- **Android Emulator**: `http://10.0.2.2:3000` (or `http://10.0.2.2:8000`)
- **iOS Simulator**: `http://localhost:3000`
- **Physical Device**: Use your local network IP (e.g. `http://192.168.1.50:3000`) or your deployed Cloud Run URL.

### 4. Run the App
```bash
# Start Metro bundler
npx expo start

# Or directly target platform:
npx expo start --ios      # Press 'i' to launch iOS Simulator
npx expo start --android  # Press 'a' to launch Android Emulator
npx expo start --web      # Press 'w' to launch Web preview
```

Scan the terminal QR code with the **Expo Go** app on your phone to run wirelessly!

---

## 📂 Project Structure

```
mobile/
├── app.json                # Expo configuration, bundle IDs, permissions & background audio
├── babel.config.js         # Babel presets & Reanimated plugin
├── package.json            # React Native, Expo, and navigation dependencies
├── tsconfig.json           # TypeScript configuration
├── App.tsx                 # Root React Native app with BottomTabNavigator & Providers
├── index.js                # Expo root component registration
└── src/
    ├── types/              # TypeScript interfaces (FeedClip, User, Comment, ShareEvent)
    ├── services/
    │   ├── api.ts          # AsyncStorage token persistence, JWT auto-refresh, EchoFlow API
    │   └── audioPlayer.ts  # Expo-AV audio manager, background audio, telemetry
    ├── context/
    │   ├── AuthContext.tsx # User session & login state
    │   └── PlayerContext.tsx # Audio queue, play/pause, like/skip, hands-free mode
    ├── screens/
    │   ├── FeedScreen.tsx    # Pull-to-refresh, vertical reels, like/comment/share
    │   ├── ExploreScreen.tsx # Category discovery & vector suggestion stream
    │   ├── UploadScreen.tsx  # Native microphone recording & audio upload
    │   ├── InboxScreen.tsx   # Social audio shares inbox
    │   └── ProfileScreen.tsx # Stats, liked clips catalog, settings
    └── components/
        ├── AudioVisualizer.tsx # Animated waveform equalizer bars
        ├── CommentModal.tsx    # Native comment bottom sheet
        └── ShareModal.tsx      # Native audio sharing modal
```
