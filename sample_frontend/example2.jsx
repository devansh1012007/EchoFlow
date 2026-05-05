import React, { useState, useEffect, useRef, createContext, useContext, useCallback } from 'react';
import { 
  Home, Compass, PlusCircle, Bell, User, Settings, Search, X, 
  Play, Pause, Heart, MessageCircle, Share2, Image as ImageIcon,
  Check, ChevronRight, Moon, Sun, Edit3, UploadCloud, LogOut, Loader2, AlertCircle
} from 'lucide-react';

// --- CONFIGURATION & GLOBAL STATE ---
const API_BASE = 'http://localhost:8005';
const CATEGORIES = ['instrumental', 'funny', 'news', 'science', 'music'];

const AuthContext = createContext(null);
const AudioPlayerContext = createContext(null);
const ThemeContext = createContext(null);

// --- UTILITY FUNCTIONS ---
const cn = (...classes) => classes.filter(Boolean).join(' ');

const fetchApi = async (endpoint, options = {}, token = null) => {
  const headers = { ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    if (options.body && typeof options.body === 'object') {
      options.body = JSON.stringify(options.body);
    }
  }

  try {
    const res = await fetch(`${API_BASE}${endpoint}`, { ...options, headers });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    
    // Check for empty responses (e.g., 204 No Content)
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  } catch (error) {
    console.error(`API Error on ${endpoint}:`, error);
    throw error;
  }
};

// --- AUTHENTICATION SCREEN ---
const AuthScreen = () => {
  const { login } = useContext(AuthContext);
  const [isLogin, setIsLogin] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  
  const [formData, setFormData] = useState({ email: '', username: '', password: '' });

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      const endpoint = isLogin ? '/auth/login/' : '/auth/register/';
      const payload = isLogin 
        ? { email: formData.email, password: formData.password }
        : { email: formData.email, username: formData.username, password: formData.password };
      
      const res = await fetchApi(endpoint, { method: 'POST', body: payload });
      if (res && res.token) {
        login(res.token, res.user);
      } else {
        throw new Error("Invalid credentials or server configuration.");
      }
    } catch (err) {
      setError(err.message || 'Authentication failed. Verify backend routes.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-gray-50 dark:bg-[#0B0F14] px-4 transition-colors duration-300">
      <div className="w-full max-w-md bg-white dark:bg-[#121820] rounded-3xl shadow-2xl border border-gray-200 dark:border-white/10 p-8">
        <div className="flex items-center space-x-2 mb-8 justify-center">
          <div className="w-6 h-6 flex space-x-[3px] items-end">
            <div className="w-1.5 h-4 bg-blue-600 dark:bg-cyan-400 rounded-sm"></div>
            <div className="w-1.5 h-6 bg-blue-600 dark:bg-cyan-400 rounded-sm"></div>
            <div className="w-1.5 h-3 bg-blue-600 dark:bg-cyan-400 rounded-sm"></div>
          </div>
          <h1 className="text-3xl font-black tracking-widest italic text-gray-900 dark:text-cyan-400 uppercase">EchoFlow</h1>
        </div>

        <h2 className="text-xl font-bold text-center mb-6 text-gray-900 dark:text-white">
          {isLogin ? 'Access your frequency' : 'Join the signal'}
        </h2>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400 text-sm flex items-start space-x-2 border border-red-200 dark:border-red-500/30">
            <AlertCircle size={18} className="flex-shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {!isLogin && (
            <div>
              <label className="block text-xs font-bold uppercase text-gray-500 mb-1">Username</label>
              <input 
                required
                type="text" 
                value={formData.username}
                onChange={(e) => setFormData({...formData, username: e.target.value})}
                className="w-full bg-gray-100 dark:bg-black/50 border border-gray-200 dark:border-white/10 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-blue-500 dark:focus:border-cyan-400 text-gray-900 dark:text-white"
              />
            </div>
          )}
          <div>
            <label className="block text-xs font-bold uppercase text-gray-500 mb-1">Email</label>
            <input 
              required
              type="email" 
              value={formData.email}
              onChange={(e) => setFormData({...formData, email: e.target.value})}
              className="w-full bg-gray-100 dark:bg-black/50 border border-gray-200 dark:border-white/10 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-blue-500 dark:focus:border-cyan-400 text-gray-900 dark:text-white"
            />
          </div>
          <div>
            <label className="block text-xs font-bold uppercase text-gray-500 mb-1">Password</label>
            <input 
              required
              type="password" 
              value={formData.password}
              onChange={(e) => setFormData({...formData, password: e.target.value})}
              className="w-full bg-gray-100 dark:bg-black/50 border border-gray-200 dark:border-white/10 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-blue-500 dark:focus:border-cyan-400 text-gray-900 dark:text-white"
            />
          </div>

          <button 
            type="submit"
            disabled={loading}
            className="w-full py-3.5 mt-2 rounded-xl font-bold transition-all disabled:opacity-50 bg-blue-600 dark:bg-cyan-400 text-white dark:text-black shadow-lg hover:opacity-90 flex justify-center items-center"
          >
            {loading ? <Loader2 size={20} className="animate-spin" /> : (isLogin ? 'Login' : 'Create Account')}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-gray-500">
          {isLogin ? "Don't have an account? " : "Already have an account? "}
          <button onClick={() => setIsLogin(!isLogin)} className="font-bold text-blue-600 dark:text-cyan-400 hover:underline">
            {isLogin ? 'Sign up' : 'Log in'}
          </button>
        </p>
      </div>
    </div>
  );
};

// --- ROOT APPLICATION ---
export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem('auth_token'));
  const [user, setUser] = useState(() => {
    const saved = localStorage.getItem('auth_user');
    return saved ? JSON.parse(saved) : null;
  });
  
  const [activeTab, setActiveTab] = useState('explore');
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark');

  // Theme Sync
  useEffect(() => {
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
    localStorage.setItem('theme', theme);
  }, [theme]);

  const toggleTheme = () => setTheme(prev => prev === 'dark' ? 'light' : 'dark');

  const login = (newToken, userData) => {
    localStorage.setItem('auth_token', newToken);
    localStorage.setItem('auth_user', JSON.stringify(userData));
    setToken(newToken);
    setUser(userData);
    setActiveTab('explore');
  };

  const logout = () => {
    localStorage.removeItem('auth_token');
    localStorage.removeItem('auth_user');
    setToken(null);
    setUser(null);
  };

  if (!token) {
    return (
      <ThemeContext.Provider value={{ theme, toggleTheme }}>
        <AuthContext.Provider value={{ login }}>
          <AuthScreen />
        </AuthContext.Provider>
      </ThemeContext.Provider>
    );
  }

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      <AuthContext.Provider value={{ token, user, logout }}>
        <AudioPlayerProvider>
          <div className="min-h-screen w-full font-sans antialiased bg-gray-100 text-gray-900 dark:bg-black dark:text-gray-100 transition-colors duration-300 flex justify-center">
            <div className="w-full h-[100dvh] flex relative bg-white dark:bg-[#0B0F14] overflow-hidden shadow-2xl transition-colors duration-300">
              
              <ResponsiveNav activeTab={activeTab} setActiveTab={setActiveTab} />
              
              <main className="flex-1 overflow-y-auto relative md:ml-64 flex justify-center pb-16 md:pb-0 bg-white dark:bg-[#0B0F14]">
                <div className={cn(
                  "w-full h-full transition-all", 
                  (activeTab === 'feed' || activeTab.endsWith('_viewer')) 
                    ? "max-w-md border-x border-gray-200 dark:border-white/5 bg-black relative z-10" 
                    : "max-w-5xl mx-auto px-4 md:px-8"
                )}>
                  {activeTab === 'feed' && <FeedScreen />}
                  {activeTab === 'explore' && <ExploreScreen onOpenFeed={() => setActiveTab('explore_viewer')} />}
                  {activeTab === 'explore_viewer' && <FeedScreen mode="explore" onBack={() => setActiveTab('explore')} />}
                  {activeTab === 'create' && <CreateScreen onComplete={() => setActiveTab('feed')} />}
                  {activeTab === 'inbox' && <InboxScreen onOpenClip={() => setActiveTab('inbox_viewer')} />}
                  {activeTab === 'inbox_viewer' && <FeedScreen mode="inbox" onBack={() => setActiveTab('inbox')} />}
                  {activeTab === 'profile' && <ProfileScreen onBack={() => setActiveTab('feed')} />}
                </div>
              </main>

            </div>
          </div>
        </AudioPlayerProvider>
      </AuthContext.Provider>
    </ThemeContext.Provider>
  );
}

// --- HLS AUDIO CONTEXT (PRODUCTION GRADE) ---
const AudioPlayerProvider = ({ children }) => {
  const [activeClip, setActiveClip] = useState(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [error, setError] = useState(null);
  
  const audioRef = useRef(new Audio());
  const hlsRef = useRef(null);

  // Load HLS.js
  useEffect(() => {
    if (typeof window !== 'undefined' && !window.Hls) {
      const script = document.createElement('script');
      script.src = 'https://cdn.jsdelivr.net/npm/hls.js@1';
      script.async = true;
      document.body.appendChild(script);
    }

    const audio = audioRef.current;
    
    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => setIsPlaying(false);
    const handleEnded = () => setIsPlaying(false);
    const handleError = (e) => setError("Audio format unsupported or network failure.");
    
    audio.addEventListener('play', handlePlay);
    audio.addEventListener('pause', handlePause);
    audio.addEventListener('ended', handleEnded);
    audio.addEventListener('error', handleError);

    return () => {
      audio.removeEventListener('play', handlePlay);
      audio.removeEventListener('pause', handlePause);
      audio.removeEventListener('ended', handleEnded);
      audio.removeEventListener('error', handleError);
      if (hlsRef.current) hlsRef.current.destroy();
    };
  }, []);

  const playTrack = useCallback((clip) => {
    const audio = audioRef.current;
    setError(null);

    // If same track, toggle play state
    if (activeClip?.id === clip.id) {
      if (isPlaying) {
        audio.pause();
      } else {
        const playPromise = audio.play();
        if (playPromise !== undefined) {
          playPromise.catch(e => setError("Playback interrupted."));
        }
      }
      return;
    }

    // New track logic
    setActiveClip(clip);
    const url = clip.audio_url || clip.audioUrl;

    if (!url) {
      setError("No audio stream available.");
      return;
    }

    // Memory cleanup
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    if (window.Hls && window.Hls.isSupported() && url.includes('.m3u8')) {
      const hls = new window.Hls();
      hlsRef.current = hls;
      
      hls.loadSource(url);
      hls.attachMedia(audio);
      
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
        audio.play().catch(err => setError("Browser prevented auto-play."));
      });

      hls.on(window.Hls.Events.ERROR, (event, data) => {
        if (data.fatal) {
          setError("HLS Streaming Error.");
          hls.destroy();
        }
      });
    } else {
      // Native fallback
      audio.src = url;
      audio.load();
      audio.play().catch(err => setError("Native playback failed."));
    }
  }, [activeClip, isPlaying]);

  const pauseTrack = useCallback(() => {
    audioRef.current.pause();
  }, []);

  return (
    <AudioPlayerContext.Provider value={{ activeClip, isPlaying, error, playTrack, pauseTrack }}>
      {children}
    </AudioPlayerContext.Provider>
  );
};

// --- NAVIGATION COMPONENT ---
const ResponsiveNav = ({ activeTab, setActiveTab }) => {
  const { token } = useContext(AuthContext);
  const [unreadCount, setUnreadCount] = useState(0);

  // Fetch Unread Count dynamically
  useEffect(() => {
    const fetchUnread = async () => {
      try {
        const data = await fetchApi('/inbox/unread/', {}, token);
        if (data && typeof data.count === 'number') {
          setUnreadCount(data.count);
        }
      } catch (err) {
        // Silent fail for background notification fetch
      }
    };
    fetchUnread();
    const interval = setInterval(fetchUnread, 30000); // Poll every 30s
    return () => clearInterval(interval);
  }, [token]);
  
  const navItems = [
    { id: 'feed', icon: Home, label: 'Feed' },
    { id: 'explore', icon: Compass, label: 'Explore' },
    { id: 'create', icon: PlusCircle, label: 'Create' },
    { id: 'inbox', icon: Bell, label: 'Inbox', badge: unreadCount },
    { id: 'profile', icon: User, label: 'Profile' },
  ];

  const { pauseTrack } = useContext(AudioPlayerContext);

  const handleNav = (id) => {
    if (activeTab === 'feed' || activeTab.endsWith('_viewer')) {
      pauseTrack(); // Pause global audio when navigating away from feeds
    }
    setActiveTab(id);
  };

  return (
    <>
      {/* Desktop Sidebar */}
      <div className="hidden md:flex flex-col w-64 h-screen bg-white dark:bg-[#080B0E] border-r border-gray-200 dark:border-white/5 fixed left-0 top-0 z-50 py-8 px-6 transition-colors duration-300">
        <div className="flex items-center space-x-2 mb-12 pl-2 cursor-pointer" onClick={() => handleNav('feed')}>
          <div className="w-5 h-5 flex space-x-[2px] items-end">
            <div className="w-1 h-3 bg-blue-600 dark:bg-cyan-400 rounded-sm"></div>
            <div className="w-1 h-5 bg-blue-600 dark:bg-cyan-400 rounded-sm"></div>
            <div className="w-1 h-2 bg-blue-600 dark:bg-cyan-400 rounded-sm"></div>
          </div>
          <h1 className="text-xl font-black tracking-widest italic text-gray-900 dark:text-cyan-400 uppercase">EchoFlow</h1>
        </div>
        
        <nav className="flex flex-col space-y-4">
          {navItems.map((item) => {
            const isActive = activeTab === item.id || activeTab === `${item.id}_viewer`;
            return (
              <button
                key={item.id}
                onClick={() => handleNav(item.id)}
                className={cn(
                  "flex items-center justify-between px-4 py-3 rounded-xl transition-all duration-300 w-full text-left group cursor-pointer",
                  isActive 
                    ? "bg-blue-50 dark:bg-cyan-500/10 text-blue-600 dark:text-cyan-400" 
                    : "text-gray-600 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-white/5"
                )}
              >
                <div className="flex items-center space-x-4">
                  <item.icon size={22} strokeWidth={isActive ? 2.5 : 2} />
                  <span className="font-bold tracking-wide">{item.label}</span>
                </div>
                {item.badge > 0 && (
                  <span className="bg-red-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full">
                    {item.badge}
                  </span>
                )}
              </button>
            )
          })}
        </nav>
      </div>

      {/* Mobile Bottom Nav */}
      <div className="md:hidden h-16 fixed bottom-0 w-full bg-white/90 dark:bg-[#0B0F14]/90 backdrop-blur-md border-t border-gray-200 dark:border-white/5 flex items-center justify-around z-50 transition-colors duration-300">
        {navItems.map((item) => {
          const isActive = activeTab === item.id || activeTab === `${item.id}_viewer`;
          return (
            <button
              key={item.id}
              onClick={() => handleNav(item.id)}
              className={cn(
                "flex flex-col items-center justify-center w-full h-full space-y-1 transition-all duration-300 relative cursor-pointer",
                isActive ? "text-blue-600 dark:text-cyan-400" : "text-gray-500 hover:text-gray-900 dark:hover:text-gray-300"
              )}
            >
              <div className="relative">
                <item.icon size={22} strokeWidth={isActive ? 2.5 : 2} />
                {item.badge > 0 && (
                  <span className="absolute -top-1 -right-2 bg-red-500 text-white text-[8px] font-bold px-1.5 py-0.5 rounded-full border border-white dark:border-[#0B0F14]">
                    {item.badge}
                  </span>
                )}
              </div>
            </button>
          )
        })}
      </div>
    </>
  );
};

// --- REUSABLE CLIP COMPONENT ---
const ClipPlayer = ({ clip, onProfileClick }) => {
  const { token } = useContext(AuthContext);
  const { activeClip, isPlaying, playTrack, error } = useContext(AudioPlayerContext);
  
  const [liked, setLiked] = useState(clip.is_liked || false);
  const [likesCount, setLikesCount] = useState(clip.likes_count || 0);
  const [following, setFollowing] = useState(false);

  // Sync state
  const isThisClipPlaying = isPlaying && activeClip?.id === clip.id;
  const thisClipError = activeClip?.id === clip.id ? error : null;

  const handleTogglePlay = () => playTrack(clip);

  const handleLike = async () => {
    const origLiked = liked;
    const origCount = likesCount;
    setLiked(!origLiked);
    setLikesCount(prev => prev + (origLiked ? -1 : 1));

    try {
      await fetchApi(`/interactions/${clip.id}/toggle-like/`, { method: 'POST' }, token);
    } catch (e) {
      // Revert on failure
      setLiked(origLiked);
      setLikesCount(origCount);
    }
  };

  const creatorName = clip.creator?.username || clip.creator_name || 'Unknown User';

  return (
    <div className="w-full h-[100dvh] md:h-[100dvh] relative bg-black overflow-hidden snap-start flex-shrink-0 border-b border-white/5">
      <div className="absolute inset-0 z-0 bg-gray-900">
        <div className="w-full h-full flex items-center justify-center opacity-20">
           <ImageIcon size={64} className="text-gray-600" />
        </div>
        {clip.cover_image && (
          <img 
            src={clip.cover_image} 
            alt="Visual" 
            className="w-full h-full object-cover opacity-60 mix-blend-overlay"
          />
        )}
        <div className="absolute inset-0 bg-gradient-to-b from-black/20 via-transparent to-black/90"></div>
      </div>

      <div className="absolute inset-0 z-10 flex flex-col justify-end p-4 md:p-6 text-white pb-24 md:pb-8">
        {thisClipError && (
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-black/80 px-4 py-2 rounded text-red-400 text-sm flex items-center shadow-lg border border-red-500/30">
            <AlertCircle size={16} className="mr-2" /> {thisClipError}
          </div>
        )}

        <div className="flex justify-between items-end">
          <div className="flex-1 pr-12">
            <div className="flex items-center space-x-3 mb-3">
              <button onClick={() => onProfileClick(clip.creator?.id)} className="font-bold text-lg hover:underline cursor-pointer tracking-wide shadow-sm">
                @{creatorName}
              </button>
              <button 
                onClick={() => setFollowing(!following)}
                className={cn(
                  "px-3 py-1 text-xs font-bold rounded-full transition-colors cursor-pointer border",
                  following ? "bg-transparent border-white/50 text-white" : "bg-white text-black border-white"
                )}
              >
                {following ? 'Following' : 'Follow'}
              </button>
            </div>
            <h2 className="text-xl font-bold mb-2 leading-tight drop-shadow-md">{clip.title}</h2>
            <div className="flex items-center space-x-2">
              <span className="bg-white/20 backdrop-blur-sm px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider font-bold">
                {clip.category || 'Audio'}
              </span>
            </div>
          </div>

          <div className="flex flex-col items-center space-y-6 pb-4">
            <button className="flex flex-col items-center space-y-1 cursor-pointer group" onClick={handleLike}>
              <div className="bg-black/40 p-3 rounded-full backdrop-blur-sm group-hover:bg-black/60 transition-colors">
                <Heart size={26} className={liked ? "fill-red-500 text-red-500" : "text-white"} />
              </div>
              <span className="text-xs font-semibold drop-shadow-md">{likesCount}</span>
            </button>
            
            <button className="flex flex-col items-center space-y-1 cursor-pointer group">
              <div className="bg-black/40 p-3 rounded-full backdrop-blur-sm group-hover:bg-black/60 transition-colors">
                <MessageCircle size={26} className="text-white" />
              </div>
              <span className="text-xs font-semibold drop-shadow-md">{clip.comments_count || 0}</span>
            </button>

            <button className="flex flex-col items-center space-y-1 cursor-pointer group">
              <div className="bg-black/40 p-3 rounded-full backdrop-blur-sm group-hover:bg-black/60 transition-colors">
                <Share2 size={26} className="text-white" />
              </div>
              <span className="text-xs font-semibold drop-shadow-md">Share</span>
            </button>
          </div>
        </div>

        <div className="w-full h-12 mt-4 bg-black/40 backdrop-blur-md rounded-xl flex items-center px-4 border border-white/10 cursor-pointer hover:bg-black/60 transition-colors" onClick={handleTogglePlay}>
          <button className="mr-4 text-white">
            {isThisClipPlaying ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" />}
          </button>
          <div className="flex-1 flex items-center justify-center space-x-1 h-6">
            {[...Array(24)].map((_, i) => (
              <div 
                key={i} 
                className={cn("w-1 rounded-full bg-cyan-400 transition-all duration-75", isThisClipPlaying ? "animate-pulse" : "opacity-30")} 
                style={{ height: isThisClipPlaying ? `${Math.max(20, Math.random() * 100)}%` : '20%' }} 
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

// --- DATA FETCHING SCREENS ---

// 1. FEED SCREEN (Used for Base Feed, Explore Results, and Inbox Viewer)
const FeedScreen = ({ mode = "feed", feedParams = null, onBack }) => {
  const { token } = useContext(AuthContext);
  const [clips, setClips] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const loadFeed = async () => {
      setLoading(true);
      setError(null);
      try {
        let endpoint = '/feed/';
        let options = {};

        // Resolve routing based on intent (Explore/Inbox integrations pass specific params)
        if (mode === "explore" && feedParams?.type === 'category') {
          endpoint = `/suggestions/?category=${feedParams.value}`;
        } else if (mode === "explore" && feedParams?.type === 'tags') {
          endpoint = '/tags/'; // The prompt specifies POST /tags/ 
          options = { method: 'POST', body: { tags: feedParams.value } };
        } else if (mode === "inbox") {
          // If viewing a specific shared item, fetch it directly
          endpoint = `/clips/${feedParams.clipId}/`;
        }

        const data = await fetchApi(endpoint, options, token);
        
        // Normalize response whether it's an array, paginated object, or single item
        if (Array.isArray(data)) setClips(data);
        else if (data?.results) setClips(data.results);
        else if (data?.id) setClips([data]); // Single item wrapper
        else setClips([]);
        
      } catch (err) {
        setError(err.message || 'Failed to initialize frequency.');
      } finally {
        setLoading(false);
      }
    };

    loadFeed();
  }, [mode, feedParams, token]);

  if (loading) return (
    <div className="h-[100dvh] w-full bg-black flex items-center justify-center">
      <Loader2 size={32} className="animate-spin text-cyan-400" />
    </div>
  );

  if (error) return (
    <div className="h-[100dvh] w-full bg-black flex flex-col items-center justify-center p-8 text-center text-white">
      <AlertCircle size={48} className="text-red-500 mb-4" />
      <p className="text-lg font-bold mb-2">Connection Severed</p>
      <p className="text-gray-400 text-sm mb-6">{error}</p>
      {onBack && (
        <button onClick={onBack} className="px-6 py-2 bg-white/10 rounded-full font-bold hover:bg-white/20 transition-colors">
          Go Back
        </button>
      )}
    </div>
  );

  if (clips.length === 0) return (
    <div className="h-[100dvh] w-full bg-black flex flex-col items-center justify-center p-8 text-center text-white">
      <ImageIcon size={48} className="text-gray-700 mb-4" />
      <p className="text-lg font-bold mb-2">The void is silent.</p>
      <p className="text-gray-500 text-sm mb-6">No clips found in this sector.</p>
      {onBack && (
        <button onClick={onBack} className="px-6 py-2 bg-white/10 rounded-full font-bold hover:bg-white/20 transition-colors">
          Go Back
        </button>
      )}
    </div>
  );

  return (
    <div className="h-[100dvh] w-full bg-black flex flex-col relative">
      {onBack && (
        <button 
          onClick={onBack} 
          className="absolute top-12 left-4 z-50 p-2 bg-black/50 backdrop-blur-md rounded-full text-white cursor-pointer hover:bg-black/80 transition-colors"
        >
          <ChevronRight className="rotate-180" size={24} />
        </button>
      )}
      <div className="flex-1 w-full h-[100dvh] overflow-y-scroll snap-y snap-mandatory scrollbar-hide">
        {clips.map(clip => (
          <ClipPlayer key={clip.id} clip={clip} onProfileClick={() => console.log('Profile integration pending')} />
        ))}
      </div>
    </div>
  );
};

// 2. EXPLORE SCREEN
const ExploreScreen = ({ onOpenFeed }) => {
  const [searchTags, setSearchTags] = useState([]);
  const [inputValue, setInputValue] = useState('');

  const handleAddTag = (e) => {
    if (e.key === 'Enter' && inputValue.trim()) {
      if (!searchTags.includes(inputValue.trim())) {
        setSearchTags([...searchTags, inputValue.trim()]);
      }
      setInputValue('');
    }
  };

  const triggerCategory = (cat) => {
    // We pass state up to the parent wrapper to inject into FeedScreen
    onOpenFeed({ type: 'category', value: cat });
  };

  const handleTagSubmit = () => {
    // Strictly mapping to the requested 'POST /tags/' structure
    onOpenFeed({ type: 'tags', value: searchTags });
  };

  return (
    <div className="h-full pt-12 md:pt-16 px-4 md:px-8 overflow-y-auto">
      <h1 className="text-3xl font-bold mb-8 text-gray-900 dark:text-white">Explore</h1>

      <section className="mb-10">
        <h2 className="text-sm font-bold uppercase tracking-wider text-gray-500 mb-4">Curated Frequencies</h2>
        <div className="flex flex-wrap gap-3">
          {CATEGORIES.map(cat => (
            <button 
              key={cat}
              onClick={() => triggerCategory(cat)}
              className="px-6 py-3 rounded-xl bg-white dark:bg-[#121820] border border-gray-200 dark:border-white/5 font-semibold capitalize shadow-sm hover:shadow-md transition-shadow cursor-pointer text-gray-800 dark:text-gray-200"
            >
              {cat}
            </button>
          ))}
        </div>
      </section>

      <section className="mb-10">
        <h2 className="text-sm font-bold uppercase tracking-wider text-gray-500 mb-4">Acoustic Tags</h2>
        
        <div className="bg-white dark:bg-[#121820] rounded-2xl p-4 border border-gray-200 dark:border-white/5 shadow-sm">
          <div className="flex flex-wrap gap-2 mb-3">
            {searchTags.map((tag, i) => (
              <span key={i} className="flex items-center space-x-1 px-3 py-1.5 bg-blue-100 dark:bg-cyan-900/30 text-blue-800 dark:text-cyan-400 rounded-lg text-sm font-medium">
                <span>#{tag}</span>
                <button onClick={() => setSearchTags(searchTags.filter((_, idx) => idx !== i))} className="cursor-pointer hover:text-black dark:hover:text-white">
                  <X size={14} />
                </button>
              </span>
            ))}
          </div>

          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
            <input 
              type="text" 
              placeholder="Type a tag and press Enter..." 
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleAddTag}
              className="w-full bg-transparent border-none focus:ring-0 text-sm pl-10 py-2 outline-none text-gray-900 dark:text-white placeholder-gray-400"
            />
          </div>
        </div>

        <button 
          onClick={handleTagSubmit}
          disabled={searchTags.length === 0}
          className="w-full mt-4 py-4 rounded-xl font-bold transition-all disabled:opacity-50 bg-blue-600 dark:bg-cyan-400 text-white dark:text-black shadow-lg cursor-pointer hover:opacity-90"
        >
          Generate Feed
        </button>
      </section>
    </div>
  );
};

// 3. INBOX SCREEN
const InboxScreen = ({ onOpenClip }) => {
  const { token } = useContext(AuthContext);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchInbox = async () => {
      try {
        const data = await fetchApi('/inbox/', {}, token);
        setEvents(data?.results || data || []);
      } catch (err) {
        setError('Failed to sync inbox.');
      } finally {
        setLoading(false);
      }
    };
    fetchInbox();
  }, [token]);

  if (loading) return <div className="h-full flex items-center justify-center"><Loader2 className="animate-spin text-blue-600 dark:text-cyan-400" /></div>;

  const unreadCount = events.filter(e => !e.is_read).length;

  return (
    <div className="h-full pt-12 md:pt-16 px-4 md:px-8 overflow-y-auto">
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-white">Inbox</h1>
        <div className="px-3 py-1 bg-red-100 dark:bg-red-500/20 text-red-600 dark:text-red-400 rounded-full text-xs font-bold tracking-wider">
          {unreadCount} NEW SHARED CLIPS
        </div>
      </div>

      {error ? (
        <div className="text-center p-8 bg-white dark:bg-[#121820] rounded-2xl border border-red-200 dark:border-red-500/30">
          <AlertCircle className="mx-auto text-red-500 mb-2" />
          <p className="text-gray-900 dark:text-white font-semibold">{error}</p>
        </div>
      ) : events.length === 0 ? (
         <div className="text-center p-8">
            <Bell size={48} className="mx-auto text-gray-300 dark:text-gray-700 mb-4" />
            <p className="text-gray-500 dark:text-gray-400">No incoming transmissions.</p>
         </div>
      ) : (
        <div className="space-y-4 pb-24">
          {events.map((item) => (
            <button 
              key={item.id} 
              onClick={() => onOpenClip({ type: 'clip', clipId: item.clip?.id })}
              className={cn(
                "w-full flex items-center p-4 rounded-2xl border transition-all cursor-pointer text-left",
                !item.is_read 
                  ? "bg-white dark:bg-[#121820] border-blue-200 dark:border-cyan-400/30 shadow-sm" 
                  : "bg-transparent border-gray-200 dark:border-white/5 opacity-70"
              )}
            >
              <div className="w-12 h-12 rounded-xl bg-gray-200 dark:bg-black/50 border border-gray-300 dark:border-white/5 mr-4 flex-shrink-0 flex items-center justify-center relative overflow-hidden">
                {item.clip?.cover_image ? (
                  <img src={item.clip.cover_image} className="w-full h-full object-cover" alt="Cover" />
                ) : (
                  <ImageIcon size={20} className="text-gray-400" />
                )}
                {!item.is_read && <div className="absolute top-1 right-1 w-2.5 h-2.5 bg-blue-500 dark:bg-cyan-400 rounded-full"></div>}
              </div>
              
              <div className="flex-1 overflow-hidden">
                <p className="text-sm font-semibold mb-0.5 truncate text-gray-900 dark:text-gray-100">
                  <span className="font-bold">{item.sender?.username || 'Someone'}</span> shared a clip
                </p>
                <p className="text-xs text-gray-500 truncate">"{item.clip?.title || 'Unknown Audio'}"</p>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

// 4. CREATE SCREEN
const CreateScreen = ({ onComplete }) => {
  const { token } = useContext(AuthContext);
  const [formData, setFormData] = useState({ name: '', description: '', category: '' });
  const [file, setFile] = useState(null);
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleFile = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file) return setError("Audio file is mandatory.");
    
    setLoading(true);
    setError(null);
    
    try {
      const formPayload = new FormData();
      formPayload.append('original_file', file);
      formPayload.append('title', formData.name);
      formPayload.append('description', formData.description);
      formPayload.append('category', formData.category);

      await fetchApi('/clips/', { method: 'POST', body: formPayload }, token);
      onComplete();
    } catch (err) {
      setError(err.message || 'Transmission failed. Retrying required.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-full pt-12 md:pt-16 px-4 md:px-8 overflow-y-auto">
      <h1 className="text-3xl font-bold mb-8 text-gray-900 dark:text-white">Upload Audio</h1>

      {error && (
        <div className="mb-6 p-4 rounded-xl bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-500/30 flex items-start space-x-2">
          <AlertCircle size={20} className="flex-shrink-0" />
          <span className="text-sm font-semibold">{error}</span>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6 pb-24">
        <label className="w-full border-2 border-dashed border-gray-300 dark:border-white/20 rounded-2xl p-8 flex flex-col items-center justify-center bg-gray-50 dark:bg-black/20 cursor-pointer hover:bg-gray-100 dark:hover:bg-black/40 transition-colors">
          <UploadCloud size={48} className={file ? "text-green-500" : "text-blue-500 dark:text-cyan-400 mb-4"} />
          <p className="font-bold text-gray-700 dark:text-gray-300 mt-4">
            {file ? file.name : 'Tap to browse files'}
          </p>
          <p className="text-xs text-gray-500 mt-2">WAV, MP3, or FLAC up to 50MB</p>
          <input type="file" accept="audio/*" onChange={handleFile} className="hidden" />
        </label>

        <div className="space-y-4">
          <div>
            <label className="block text-xs font-bold uppercase tracking-wider text-gray-500 mb-2">Clip Name</label>
            <input 
              required
              type="text" 
              value={formData.name}
              onChange={(e) => setFormData({...formData, name: e.target.value})}
              className="w-full bg-white dark:bg-[#121820] border border-gray-200 dark:border-white/10 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-blue-500 dark:focus:border-cyan-400 text-gray-900 dark:text-white"
              placeholder="Give your audio a title"
            />
          </div>

          <div>
            <label className="block text-xs font-bold uppercase tracking-wider text-gray-500 mb-2">Description</label>
            <textarea 
              value={formData.description}
              onChange={(e) => setFormData({...formData, description: e.target.value})}
              className="w-full bg-white dark:bg-[#121820] border border-gray-200 dark:border-white/10 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-blue-500 dark:focus:border-cyan-400 resize-none h-24 text-gray-900 dark:text-white"
              placeholder="Context or story behind the clip"
            />
          </div>

          <div>
            <label className="block text-xs font-bold uppercase tracking-wider text-gray-500 mb-2">Mandatory Category</label>
            <select 
              required
              value={formData.category}
              onChange={(e) => setFormData({...formData, category: e.target.value})}
              className="w-full bg-white dark:bg-[#121820] border border-gray-200 dark:border-white/10 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-blue-500 dark:focus:border-cyan-400 text-gray-900 dark:text-white appearance-none"
            >
              <option value="" disabled>Select a category...</option>
              {CATEGORIES.map(cat => <option key={cat} value={cat} className="capitalize">{cat}</option>)}
            </select>
          </div>
        </div>

        <button 
          type="submit"
          disabled={loading}
          className="w-full py-4 rounded-xl font-bold transition-all disabled:opacity-50 bg-blue-600 dark:bg-cyan-400 text-white dark:text-black shadow-lg cursor-pointer hover:opacity-90 flex justify-center items-center"
        >
          {loading ? <Loader2 className="animate-spin" size={24} /> : 'Publish Clip'}
        </button>
      </form>
    </div>
  );
};

// 5. PROFILE SCREEN
const ProfileScreen = () => {
  const { theme, toggleTheme } = useContext(ThemeContext);
  const { token, logout, user } = useContext(AuthContext);
  
  const [profile, setProfile] = useState(null);
  const [likedClips, setLikedClips] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editMode, setEditMode] = useState(false);
  const [editName, setEditName] = useState('');

  useEffect(() => {
    const fetchProfile = async () => {
      try {
        // Fetch user data
        const userData = await fetchApi('/users/me/', {}, token);
        setProfile(userData);
        setEditName(userData?.username || '');

        // Fetch liked clips separately to avoid nested heavy payloads
        const likesData = await fetchApi('/users/me/liked_clips/', {}, token);
        setLikedClips(likesData?.results || likesData || []);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchProfile();
  }, [token]);

  const handleUpdateProfile = async () => {
    try {
      const updated = await fetchApi('/users/me/', { method: 'PUT', body: { username: editName } }, token);
      setProfile(updated);
      setEditMode(false);
    } catch(err) {
      alert("Failed to update profile.");
    }
  };

  const handleAvatarUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('avatar', file);
    try {
      const updated = await fetchApi('/users/me/avatar/', { method: 'PUT', body: fd }, token);
      setProfile(updated);
    } catch(err) {
      alert("Avatar upload failed.");
    }
  };

  if (loading) return <div className="h-full flex items-center justify-center"><Loader2 className="animate-spin text-blue-600 dark:text-cyan-400" /></div>;

  return (
    <div className="h-full pt-12 md:pt-16 overflow-y-auto">
      <div className="px-4 md:px-8 flex items-start justify-between mb-8">
        
        <div className="flex items-center space-x-4">
          <label className="w-20 h-20 rounded-full bg-gray-200 dark:bg-gray-800 border-2 border-white dark:border-black shadow-lg relative overflow-hidden group cursor-pointer">
            {profile?.avatar_url ? (
               <img src={profile.avatar_url} alt="Profile" className="w-full h-full object-cover" />
            ) : (
               <User size={32} className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-gray-400" />
            )}
            <div className="absolute inset-0 bg-black/50 hidden group-hover:flex items-center justify-center text-white">
              <UploadCloud size={20} />
            </div>
            <input type="file" accept="image/*" onChange={handleAvatarUpload} className="hidden" />
          </label>
          
          <div>
            <div className="flex items-center space-x-2">
              {editMode ? (
                <div className="flex space-x-2">
                   <input type="text" value={editName} onChange={e=>setEditName(e.target.value)} className="bg-gray-100 dark:bg-black/50 border border-gray-200 dark:border-white/10 rounded px-2 py-1 text-sm text-gray-900 dark:text-white" />
                   <button onClick={handleUpdateProfile} className="text-green-500"><Check size={18} /></button>
                </div>
              ) : (
                <>
                  <h1 className="text-2xl font-bold text-gray-900 dark:text-white">{profile?.username || user?.username || 'User'}</h1>
                  <button onClick={() => setEditMode(true)} className="text-gray-400 hover:text-blue-500 cursor-pointer transition-colors"><Edit3 size={16} /></button>
                </>
              )}
            </div>
            <p className="text-sm text-gray-500">{profile?.email || user?.email}</p>
          </div>
        </div>

        <div className="flex space-x-2">
          <button 
            onClick={toggleTheme}
            className="p-3 rounded-full bg-gray-100 dark:bg-white/5 border border-gray-200 dark:border-white/10 text-gray-600 dark:text-gray-300 cursor-pointer hover:bg-gray-200 dark:hover:bg-white/10 transition-colors"
          >
            {theme === 'dark' ? <Sun size={20} /> : <Moon size={20} />}
          </button>
          <button 
            onClick={logout}
            className="p-3 rounded-full bg-red-100 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 text-red-600 dark:text-red-400 cursor-pointer hover:bg-red-200 dark:hover:bg-red-500/20 transition-colors"
          >
            <LogOut size={20} />
          </button>
        </div>
      </div>

      <div className="px-4 md:px-8 flex justify-between border-b border-gray-200 dark:border-white/5 pb-8 mb-6">
        <div className="text-center flex-1">
          <p className="text-xl font-bold text-gray-900 dark:text-white">{profile?.total_uploads || 0}</p>
          <p className="text-[10px] uppercase tracking-widest font-bold text-gray-500 mt-1">Uploads</p>
        </div>
        <div className="text-center flex-1">
          <p className="text-xl font-bold text-gray-900 dark:text-white">{profile?.followers_count || 0}</p>
          <p className="text-[10px] uppercase tracking-widest font-bold text-gray-500 mt-1">Followers</p>
        </div>
        <div className="text-center flex-1">
          <p className="text-xl font-bold text-gray-900 dark:text-white">{profile?.following_count || 0}</p>
          <p className="text-[10px] uppercase tracking-widest font-bold text-gray-500 mt-1">Following</p>
        </div>
      </div>

      <div className="px-4 md:px-8 pb-24">
        <h2 className="text-sm font-bold uppercase tracking-wider text-gray-500 mb-4">Liked Audio Tracks</h2>
        {likedClips.length === 0 ? (
          <div className="text-center py-8 text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-[#121820] rounded-2xl border border-dashed border-gray-200 dark:border-white/10">
            No acoustic resonance found.
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {likedClips.map(clip => (
              <div key={clip.id} className="aspect-square bg-gray-200 dark:bg-[#121820] rounded-2xl p-4 flex flex-col justify-end relative overflow-hidden border border-gray-200 dark:border-white/5 cursor-pointer group">
                 {clip.cover_image && <img src={clip.cover_image} alt="Cover" className="absolute inset-0 w-full h-full object-cover opacity-40 group-hover:opacity-60 transition-opacity" />}
                 <div className="absolute inset-0 bg-gradient-to-t from-black/80 to-transparent"></div>
                 <div className="relative z-10 text-white">
                   <h3 className="font-bold text-sm truncate drop-shadow-sm">{clip.title}</h3>
                   <p className="text-xs text-gray-300 drop-shadow-sm">{clip.creator?.username}</p>
                 </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};