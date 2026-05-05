import React, { useState, useEffect, useRef, createContext, useContext } from 'react';
import { 
  Home, Compass, PlusCircle, Bell, User, Settings, Search, X, 
  Play, Pause, Heart, MessageCircle, Share2, Image as ImageIcon,
  Check, ChevronRight, Moon, Sun, Edit3, UploadCloud
} from 'lucide-react';

// --- GLOBAL CONTEXTS ---
const API_BASE = 'http://localhost:8005';
const AuthContext = createContext(null);
const AudioPlayerContext = createContext(null);
const ThemeContext = createContext(null);

// --- UTILITIES ---
const formatTime = (seconds) => {
  if (!seconds || isNaN(seconds)) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
};

const cn = (...classes) => classes.filter(Boolean).join(' ');

// --- DUMMY DATA (WITH REAL HLS TEST STREAMS) ---
const MOCK_CLIPS = [
  { id: 1, creator: 'Dr. Acoustic', title: 'The Physics of Sound', category: 'science', likes: 1205, comments: 42, isLiked: false, audioUrl: 'https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8' },
  { id: 2, creator: 'Daily Brief', title: 'Market Updates', category: 'news', likes: 850, comments: 12, isLiked: true, audioUrl: 'https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_ts/master.m3u8' },
  { id: 3, creator: 'String Theory', title: 'Acoustic Guitar Session', category: 'instrumental', likes: 3400, comments: 156, isLiked: false, audioUrl: 'https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8' },
  { id: 4, creator: 'Standup Central', title: 'Crowd Work Clip', category: 'funny', likes: 8900, comments: 405, isLiked: true, audioUrl: 'https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_ts/master.m3u8' },
  { id: 5, creator: 'Synthwave', title: 'Midnight Drive', category: 'music', likes: 5600, comments: 210, isLiked: false, audioUrl: 'https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8' },
];

const MOCK_INBOX = [
  { id: 101, sender: 'Alex M.', timestamp: '2h ago', clip: MOCK_CLIPS[2], read: false },
  { id: 102, sender: 'Sarah K.', timestamp: '5h ago', clip: MOCK_CLIPS[0], read: false },
  { id: 103, sender: 'Jordan T.', timestamp: '1d ago', clip: MOCK_CLIPS[3], read: true },
];

const CATEGORIES = ['instrumental', 'funny', 'news', 'science', 'music'];

// --- ROOT APPLICATION ---
export default function App() {
  const [token, setToken] = useState('mock-token');
  const [activeTab, setActiveTab] = useState('feed');
  const [theme, setTheme] = useState('dark');

  const toggleTheme = () => setTheme(prev => prev === 'dark' ? 'light' : 'dark');

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      <AuthContext.Provider value={{ token, setToken }}>
        <AudioPlayerProvider>
          <div className={cn(
            "min-h-screen w-full font-sans antialiased transition-colors duration-300 flex justify-center",
            theme === 'dark' ? "dark bg-black text-gray-100" : "bg-gray-100 text-gray-900"
          )}>
            <div className="w-full h-[100dvh] flex relative bg-white dark:bg-[#0B0F14] overflow-hidden shadow-2xl transition-colors duration-300">
              
              <ResponsiveNav activeTab={activeTab} setActiveTab={setActiveTab} />
              
              <main className="flex-1 overflow-y-auto relative md:ml-64 flex justify-center pb-16 md:pb-0">
                <div className={cn(
                  "w-full h-full transition-all", 
                  (activeTab === 'feed' || activeTab === 'inbox_viewer' || activeTab === 'explore_viewer') 
                    ? "max-w-md border-x border-gray-200 dark:border-white/5 bg-gray-50 dark:bg-[#080b10] shadow-2xl relative z-10" 
                    : "max-w-5xl mx-auto px-4 md:px-8"
                )}>
                  {activeTab === 'feed' && <FeedScreen />}
                  {activeTab === 'explore' && <ExploreScreen onOpenFeed={() => setActiveTab('explore_viewer')} />}
                  {activeTab === 'explore_viewer' && <FeedScreen onBack={() => setActiveTab('explore')} />}
                  {activeTab === 'create' && <CreateScreen onComplete={() => setActiveTab('feed')} />}
                  {activeTab === 'inbox' && <InboxScreen onOpenClip={() => setActiveTab('inbox_viewer')} />}
                  {activeTab === 'inbox_viewer' && <FeedScreen singleClipMode={true} onBack={() => setActiveTab('inbox')} />}
                  {activeTab === 'profile' && <ProfileScreen />}
                </div>
              </main>

            </div>
          </div>
        </AudioPlayerProvider>
      </AuthContext.Provider>
    </ThemeContext.Provider>
  );
}

// --- HLS AUDIO CONTEXT ---
const AudioPlayerProvider = ({ children }) => {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentUrl, setCurrentUrl] = useState(null);
  const audioRef = useRef(new Audio());
  const hlsRef = useRef(null);

  // Initialize HLS.js dynamically for the preview environment
  useEffect(() => {
    if (typeof window !== 'undefined' && !window.Hls) {
      const script = document.createElement('script');
      script.src = 'https://cdn.jsdelivr.net/npm/hls.js@1';
      script.async = true;
      document.body.appendChild(script);
    }

    const audio = audioRef.current;
    
    // Maintain state strictly
    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => setIsPlaying(false);
    const handleEnded = () => setIsPlaying(false);
    
    audio.addEventListener('play', handlePlay);
    audio.addEventListener('pause', handlePause);
    audio.addEventListener('ended', handleEnded);

    return () => {
      audio.removeEventListener('play', handlePlay);
      audio.removeEventListener('pause', handlePause);
      audio.removeEventListener('ended', handleEnded);
      if (hlsRef.current) {
        hlsRef.current.destroy();
      }
    };
  }, []);

  const playTrack = (url) => {
    const audio = audioRef.current;

    // Toggle logic for the same track
    if (currentUrl === url) {
      if (isPlaying) {
        audio.pause();
      } else {
        audio.play().catch(console.error);
      }
      return;
    }

    // New track logic
    setCurrentUrl(url);

    // CRITICAL: Destroy old HLS instance before creating a new one to prevent memory leaks
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    // Initialize new stream
    if (window.Hls && window.Hls.isSupported() && url.includes('.m3u8')) {
      const hls = new window.Hls();
      hlsRef.current = hls;
      
      hls.loadSource(url);
      hls.attachMedia(audio);
      
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
        audio.play().catch(err => console.error("Playback prevented by browser policy:", err));
      });

      hls.on(window.Hls.Events.ERROR, (event, data) => {
        if (data.fatal) {
          console.error("Fatal HLS Error:", data);
          hls.destroy();
        }
      });
    } else {
      // Fallback for Safari natively supporting HLS, or standard files
      audio.src = url;
      audio.load();
      audio.play().catch(err => console.error("Native playback prevented:", err));
    }
  };

  return (
    <AudioPlayerContext.Provider value={{ isPlaying, currentUrl, playTrack }}>
      {children}
    </AudioPlayerContext.Provider>
  );
};

// --- NAVIGATION ---
const ResponsiveNav = ({ activeTab, setActiveTab }) => {
  const unreadCount = MOCK_INBOX.filter(i => !i.read).length;
  
  const navItems = [
    { id: 'feed', icon: Home, label: 'Feed' },
    { id: 'explore', icon: Compass, label: 'Explore' },
    { id: 'create', icon: PlusCircle, label: 'Create' },
    { id: 'inbox', icon: Bell, label: 'Inbox', badge: unreadCount },
    { id: 'profile', icon: User, label: 'Profile' },
  ];

  const handleNav = (id) => {
    setActiveTab(id);
  };

  return (
    <>
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
  const { isPlaying, currentUrl, playTrack } = useContext(AudioPlayerContext);
  const [liked, setLiked] = useState(clip.isLiked);
  const [following, setFollowing] = useState(false);

  // Check if this specific clip is the one currently playing via the HLS provider
  const isThisClipPlaying = isPlaying && currentUrl === clip.audioUrl;

  const handleTogglePlay = () => {
    playTrack(clip.audioUrl);
  };

  return (
    <div className="w-full h-full relative bg-gray-900 overflow-hidden snap-start flex-shrink-0">
      <div className="absolute inset-0 z-0">
        <div className="w-full h-full flex items-center justify-center opacity-30">
           <ImageIcon size={64} className="text-gray-600" />
        </div>
        <img 
          src={`https://picsum.photos/seed/${clip.id}/800/1200`} 
          alt="Visual" 
          className="w-full h-full object-cover opacity-60 mix-blend-overlay"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-black/20 via-transparent to-black/90"></div>
      </div>

      <div className="absolute inset-0 z-10 flex flex-col justify-end p-4 md:p-6 text-white pb-24 md:pb-6">
        <div className="flex justify-between items-end">
          
          <div className="flex-1 pr-12">
            <div className="flex items-center space-x-3 mb-3">
              <button onClick={onProfileClick} className="font-bold text-lg hover:underline cursor-pointer tracking-wide shadow-sm">
                @{clip.creator}
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
                {clip.category}
              </span>
            </div>
          </div>

          <div className="flex flex-col items-center space-y-6 pb-4">
            <button className="flex flex-col items-center space-y-1 cursor-pointer group" onClick={() => setLiked(!liked)}>
              <div className="bg-black/40 p-3 rounded-full backdrop-blur-sm group-hover:bg-black/60 transition-colors">
                <Heart size={26} className={liked ? "fill-red-500 text-red-500" : "text-white"} />
              </div>
              <span className="text-xs font-semibold drop-shadow-md">{clip.likes + (liked && !clip.isLiked ? 1 : 0)}</span>
            </button>
            
            <button className="flex flex-col items-center space-y-1 cursor-pointer group">
              <div className="bg-black/40 p-3 rounded-full backdrop-blur-sm group-hover:bg-black/60 transition-colors">
                <MessageCircle size={26} className="text-white" />
              </div>
              <span className="text-xs font-semibold drop-shadow-md">{clip.comments}</span>
            </button>

            <button className="flex flex-col items-center space-y-1 cursor-pointer group">
              <div className="bg-black/40 p-3 rounded-full backdrop-blur-sm group-hover:bg-black/60 transition-colors">
                <Share2 size={26} className="text-white" />
              </div>
              <span className="text-xs font-semibold drop-shadow-md">Share</span>
            </button>
          </div>
        </div>

        <div className="w-full h-12 mt-4 bg-black/40 backdrop-blur-md rounded-xl flex items-center px-4 border border-white/10 cursor-pointer" onClick={handleTogglePlay}>
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

// --- SCREENS ---
const FeedScreen = ({ singleClipMode = false, onBack }) => {
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
      <div className="flex-1 w-full overflow-y-scroll snap-y snap-mandatory scrollbar-hide">
        {singleClipMode ? (
          <ClipPlayer clip={MOCK_CLIPS[0]} onProfileClick={() => console.log('Navigate to profile')} />
        ) : (
          MOCK_CLIPS.map(clip => (
            <ClipPlayer key={clip.id} clip={clip} onProfileClick={() => console.log('Navigate to profile')} />
          ))
        )}
      </div>
    </div>
  );
};

const ExploreScreen = ({ onOpenFeed }) => {
  const [searchTags, setSearchTags] = useState([]);
  const [inputValue, setInputValue] = useState('');

  const handleAddTag = (e) => {
    if (e.key === 'Enter' && inputValue.trim()) {
      setSearchTags([...searchTags, inputValue.trim()]);
      setInputValue('');
    }
  };

  const handleNext = () => {
    console.log('API Request Triggered with tags:', { tags: searchTags });
    onOpenFeed();
  };

  return (
    <div className="h-full pt-12 md:pt-16 px-4 md:px-8 overflow-y-auto">
      <h1 className="text-3xl font-bold mb-8">Explore</h1>

      <section className="mb-10">
        <h2 className="text-sm font-bold uppercase tracking-wider text-gray-500 mb-4">Curated Categories</h2>
        <div className="flex flex-wrap gap-3">
          {CATEGORIES.map(cat => (
            <button 
              key={cat}
              onClick={() => {
                console.log(`GET /suggestions/?category=${cat}`);
                onOpenFeed();
              }}
              className="px-6 py-3 rounded-xl bg-white dark:bg-[#121820] border border-gray-200 dark:border-white/5 font-semibold capitalize shadow-sm hover:shadow-md transition-shadow cursor-pointer text-gray-800 dark:text-gray-200"
            >
              {cat}
            </button>
          ))}
        </div>
      </section>

      <section className="mb-10">
        <h2 className="text-sm font-bold uppercase tracking-wider text-gray-500 mb-4">Custom Tag Search</h2>
        
        <div className="bg-white dark:bg-[#121820] rounded-2xl p-4 border border-gray-200 dark:border-white/5 shadow-sm">
          <div className="flex flex-wrap gap-2 mb-3">
            {searchTags.map((tag, i) => (
              <span key={i} className="flex items-center space-x-1 px-3 py-1.5 bg-blue-100 dark:bg-cyan-900/30 text-blue-800 dark:text-cyan-400 rounded-lg text-sm font-medium">
                <span>#{tag}</span>
                <button onClick={() => setSearchTags(searchTags.filter((_, idx) => idx !== i))} className="cursor-pointer">
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
          onClick={handleNext}
          disabled={searchTags.length === 0}
          className="w-full mt-4 py-4 rounded-xl font-bold transition-all disabled:opacity-50 bg-blue-600 dark:bg-cyan-400 text-white dark:text-black shadow-lg cursor-pointer"
        >
          Generate Feed via Tags
        </button>
      </section>
    </div>
  );
};

const InboxScreen = ({ onOpenClip }) => {
  return (
    <div className="h-full pt-12 md:pt-16 px-4 md:px-8 overflow-y-auto">
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-3xl font-bold">Inbox</h1>
        <div className="px-3 py-1 bg-red-100 dark:bg-red-500/20 text-red-600 dark:text-red-400 rounded-full text-xs font-bold tracking-wider">
          {MOCK_INBOX.filter(i => !i.read).length} NEW SHARED CLIPS
        </div>
      </div>

      <div className="space-y-4 pb-24">
        {MOCK_INBOX.map((item) => (
          <button 
            key={item.id} 
            onClick={onOpenClip}
            className={cn(
              "w-full flex items-center p-4 rounded-2xl border transition-all cursor-pointer text-left",
              !item.read 
                ? "bg-white dark:bg-[#121820] border-blue-200 dark:border-cyan-400/30 shadow-sm" 
                : "bg-transparent border-gray-200 dark:border-white/5 opacity-70"
            )}
          >
            <div className="w-12 h-12 rounded-xl bg-gray-200 dark:bg-black/50 border border-gray-300 dark:border-white/5 mr-4 flex-shrink-0 flex items-center justify-center relative overflow-hidden">
              <ImageIcon size={20} className="text-gray-400" />
              {!item.read && <div className="absolute top-1 right-1 w-2.5 h-2.5 bg-blue-500 dark:bg-cyan-400 rounded-full"></div>}
            </div>
            
            <div className="flex-1 overflow-hidden">
              <p className="text-sm font-semibold mb-0.5 truncate text-gray-900 dark:text-gray-100">
                <span className="font-bold">{item.sender}</span> shared a clip
              </p>
              <p className="text-xs text-gray-500 truncate">"{item.clip.title}"</p>
            </div>
            
            <span className="text-xs font-medium text-gray-400 ml-4">{item.timestamp}</span>
          </button>
        ))}
      </div>
    </div>
  );
};

const CreateScreen = ({ onComplete }) => {
  const [formData, setFormData] = useState({ name: '', description: '', category: '' });

  const handleSubmit = (e) => {
    e.preventDefault();
    console.log('POST /clips/', formData);
    onComplete();
  };

  return (
    <div className="h-full pt-12 md:pt-16 px-4 md:px-8 overflow-y-auto">
      <h1 className="text-3xl font-bold mb-8">Upload Audio</h1>

      <form onSubmit={handleSubmit} className="space-y-6 pb-24">
        <div className="w-full border-2 border-dashed border-gray-300 dark:border-white/20 rounded-2xl p-8 flex flex-col items-center justify-center bg-gray-50 dark:bg-black/20 cursor-pointer hover:bg-gray-100 dark:hover:bg-black/40 transition-colors">
          <UploadCloud size={48} className="text-blue-500 dark:text-cyan-400 mb-4" />
          <p className="font-bold text-gray-700 dark:text-gray-300">Tap to browse files</p>
          <p className="text-xs text-gray-500 mt-2">WAV, MP3, or FLAC up to 50MB</p>
        </div>

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
              {CATEGORIES.map(cat => <option key={cat} value={cat}>{cat}</option>)}
            </select>
          </div>
        </div>

        <button 
          type="submit"
          className="w-full py-4 rounded-xl font-bold transition-all bg-gray-900 dark:bg-cyan-400 text-white dark:text-black shadow-lg cursor-pointer hover:opacity-90"
        >
          Publish Clip
        </button>
      </form>
    </div>
  );
};

const ProfileScreen = () => {
  const { theme, toggleTheme } = useContext(ThemeContext);
  
  return (
    <div className="h-full pt-12 md:pt-16 overflow-y-auto">
      <div className="px-4 md:px-8 flex items-start justify-between mb-8">
        <div className="flex items-center space-x-4">
          <div className="w-20 h-20 rounded-full bg-gray-200 dark:bg-gray-800 border-2 border-white dark:border-black shadow-lg relative overflow-hidden group cursor-pointer">
            <img src="https://i.pravatar.cc/150?img=11" alt="Profile" className="w-full h-full object-cover" />
            <div className="absolute inset-0 bg-black/50 hidden group-hover:flex items-center justify-center text-white">
              <UploadCloud size={20} />
            </div>
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Alex M.</h1>
              <button className="text-gray-400 hover:text-blue-500 cursor-pointer transition-colors"><Edit3 size={16} /></button>
            </div>
            <p className="text-sm text-gray-500">@alex_audio</p>
          </div>
        </div>

        <button 
          onClick={toggleTheme}
          className="p-3 rounded-full bg-gray-100 dark:bg-white/5 border border-gray-200 dark:border-white/10 text-gray-600 dark:text-gray-300 cursor-pointer hover:bg-gray-200 dark:hover:bg-white/10 transition-colors"
        >
          {theme === 'dark' ? <Sun size={20} /> : <Moon size={20} />}
        </button>
      </div>

      <div className="px-4 md:px-8 flex justify-between border-b border-gray-200 dark:border-white/5 pb-8 mb-6">
        <div className="text-center flex-1">
          <p className="text-xl font-bold text-gray-900 dark:text-white">42</p>
          <p className="text-[10px] uppercase tracking-widest font-bold text-gray-500 mt-1">Uploads</p>
        </div>
        <div className="text-center flex-1">
          <p className="text-xl font-bold text-gray-900 dark:text-white">12.5K</p>
          <p className="text-[10px] uppercase tracking-widest font-bold text-gray-500 mt-1">Followers</p>
        </div>
        <div className="text-center flex-1">
          <p className="text-xl font-bold text-gray-900 dark:text-white">850</p>
          <p className="text-[10px] uppercase tracking-widest font-bold text-gray-500 mt-1">Following</p>
        </div>
      </div>

      <div className="px-4 md:px-8 pb-24">
        <h2 className="text-sm font-bold uppercase tracking-wider text-gray-500 mb-4">Liked Audio Tracks</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {MOCK_CLIPS.map(clip => (
            <div key={clip.id} className="aspect-square bg-gray-200 dark:bg-[#121820] rounded-2xl p-4 flex flex-col justify-end relative overflow-hidden border border-gray-200 dark:border-white/5 cursor-pointer group">
               <img src={`https://picsum.photos/seed/${clip.id+10}/400/400`} alt="Cover" className="absolute inset-0 w-full h-full object-cover opacity-40 group-hover:opacity-60 transition-opacity" />
               <div className="absolute inset-0 bg-gradient-to-t from-black/80 to-transparent"></div>
               <div className="relative z-10 text-white">
                 <h3 className="font-bold text-sm truncate drop-shadow-sm">{clip.title}</h3>
                 <p className="text-xs text-gray-300 drop-shadow-sm">{clip.creator}</p>
               </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};