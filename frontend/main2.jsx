import React, { useState, useEffect, useRef, createContext, useContext } from 'react';
import { 
  Home, Compass, PlusCircle, Bell, User, Settings, SlidersHorizontal, 
  Play, Pause, Heart, MessageCircle, Share2, Search, X, Check, ChevronLeft,
  Grid, List as ListIcon, Loader2
} from 'lucide-react';

// --- GLOBAL CONTEXT & STATE ---
const API_BASE = 'http://localhost:8005';
const AuthContext = createContext(null);
const AudioPlayerContext = createContext(null);

// --- UTILITIES ---
const formatTime = (seconds) => {
  if (!seconds || isNaN(seconds)) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
};

const cn = (...classes) => classes.filter(Boolean).join(' ');

// --- DUMMY DATA ---
const DUMMY_SAVED_CLIPS = [
  { id: 1, title: 'Synapse Echo', creator: 'Kaelen Drift', image: 'bg-cyan-900/40', color: 'text-cyan-400' },
  { id: 2, title: 'Void Frequencies', creator: 'Aura Architect', image: 'bg-purple-900/40', color: 'text-purple-400' },
  { id: 3, title: 'Submerged', creator: 'Deep Sequence', image: 'bg-blue-900/40', color: 'text-blue-400' },
  { id: 4, title: 'Liquid State', creator: 'Nova Pulse', image: 'bg-indigo-900/40', color: 'text-indigo-400' },
  { id: 5, title: 'Resonance', creator: 'Echo Chamber', image: 'bg-pink-900/40', color: 'text-pink-400' },
  { id: 6, title: 'Static Waves', creator: 'Sudo.WAV', image: 'bg-green-900/40', color: 'text-green-400' }
];

const DUMMY_EXPLORE_CLIPS = [
  { id: 101, title: 'Neon Genesis Frequencies', creator: 'Sudo.WAV', duration: '3:42', color: 'text-cyan-400', active: false },
  { id: 102, title: 'Late Night Synthesizer', creator: 'Analog Dreams', duration: '5:18', color: 'text-pink-400', active: false },
  { id: 103, title: 'Atmospheric Drift', creator: 'Dr. Echo', duration: '2:15', color: 'text-cyan-400', active: true },
  { id: 104, title: 'Deep Bass Mechanics', creator: 'Sub_Culture', duration: '2:15', color: 'text-gray-400', active: false },
  { id: 105, title: 'Lo-Fi Study Session', creator: 'Chill Waves', duration: '45:00', color: 'text-blue-400', active: false },
  { id: 106, title: 'Ambient Rain', creator: 'Nature Sounds', duration: '12:00', color: 'text-indigo-400', active: false }
];

// --- MAIN APP WRAPPER ---
export default function App() {
  const [token, setToken] = useState(null);
  const [activeTab, setActiveTab] = useState('feed');
  const [onboardingComplete, setOnboardingComplete] = useState(false);

  useEffect(() => {
    const savedToken = localStorage.getItem('token');
    if (savedToken) setToken(savedToken);
  }, []);

  const login = (token) => {
    localStorage.setItem('token', token);
    setToken(token);
  };

  return (
    <AuthContext.Provider value={{ token, login }}>
      <AudioPlayerProvider>
        <div className="flex justify-center bg-black min-h-screen text-gray-100 font-sans antialiased selection:bg-cyan-500/30">
          <div className="w-full h-[100dvh] flex relative bg-[#0B0F14] overflow-hidden shadow-2xl">
            
            {!onboardingComplete ? (
               <div className="w-full max-w-md mx-auto border-x border-white/5 bg-[#080b10]">
                 <OnboardingFlow onComplete={() => setOnboardingComplete(true)} />
               </div>
            ) : (
              <>
                <ResponsiveNav activeTab={activeTab} setActiveTab={setActiveTab} />
                <main className="flex-1 overflow-y-auto relative md:ml-64 flex justify-center pb-16 md:pb-0">
                  <div className={cn("w-full h-full transition-all", activeTab === 'feed' ? "max-w-md border-x border-white/5 bg-[#080b10] shadow-2xl relative z-10" : "max-w-6xl mx-auto")}>
                    {activeTab === 'feed' && <FeedScreen />}
                    {activeTab === 'explore' && <ExploreScreen />}
                    {activeTab === 'create' && <UploadScreen onCancel={() => setActiveTab('feed')} />}
                    {activeTab === 'notifications' && <SavedScreen />}
                    {activeTab === 'profile' && <ProfileScreen />}
                  </div>
                </main>
              </>
            )}

          </div>
        </div>
      </AudioPlayerProvider>
    </AuthContext.Provider>
  );
}

// --- AUDIO PLAYER CONTEXT ---
const AudioPlayerProvider = ({ children }) => {
  const [currentTrack, setCurrentTrack] = useState(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);
  const audioRef = useRef(new Audio());

  useEffect(() => {
    const audio = audioRef.current;
    const updateTime = () => setProgress(audio.currentTime);
    const updateDuration = () => setDuration(audio.duration);
    const onEnded = () => setIsPlaying(false);

    audio.addEventListener('timeupdate', updateTime);
    audio.addEventListener('loadedmetadata', updateDuration);
    audio.addEventListener('ended', onEnded);

    return () => {
      audio.removeEventListener('timeupdate', updateTime);
      audio.removeEventListener('loadedmetadata', updateDuration);
      audio.removeEventListener('ended', onEnded);
    };
  }, []);

  const playTrack = (track) => {
    if (currentTrack?.id === track.id) {
      togglePlay();
      return;
    }
    setCurrentTrack(track);
    audioRef.current.src = track.preview_url || 'https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3';
    audioRef.current.play().then(() => setIsPlaying(true)).catch(console.error);
  };

  const togglePlay = () => {
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play().catch(console.error);
    }
    setIsPlaying(!isPlaying);
  };

  return (
    <AudioPlayerContext.Provider value={{ currentTrack, isPlaying, progress, duration, playTrack, togglePlay }}>
      {children}
    </AudioPlayerContext.Provider>
  );
};

// --- COMPONENTS ---
const ResponsiveNav = ({ activeTab, setActiveTab }) => {
  const navItems = [
    { id: 'feed', icon: Home, label: 'Feed' },
    { id: 'explore', icon: Compass, label: 'Explore' },
    { id: 'create', icon: PlusCircle, label: 'Create' },
    { id: 'notifications', icon: Bell, label: 'Saved' },
    { id: 'profile', icon: User, label: 'Profile' },
  ];

  return (
    <>
      {/* Desktop Sidebar */}
      <div className="hidden md:flex flex-col w-64 h-screen bg-[#080B0E] border-r border-white/5 fixed left-0 top-0 z-50 py-8 px-6">
        <div className="flex items-center space-x-2 mb-12 pl-2 cursor-pointer" onClick={() => setActiveTab('feed')}>
          <div className="w-5 h-5 flex space-x-[2px] items-end">
            <div className="w-1 h-3 bg-cyan-400 rounded-sm shadow-[0_0_8px_rgba(34,211,238,0.6)] animate-pulse"></div>
            <div className="w-1 h-5 bg-cyan-400 rounded-sm shadow-[0_0_8px_rgba(34,211,238,0.6)] animate-pulse delay-75"></div>
            <div className="w-1 h-2 bg-cyan-400 rounded-sm shadow-[0_0_8px_rgba(34,211,238,0.6)] animate-pulse delay-150"></div>
          </div>
          <h1 className="text-xl font-black tracking-widest italic text-cyan-400 drop-shadow-[0_0_10px_rgba(34,211,238,0.3)] uppercase">EchoFlow</h1>
        </div>
        
        <nav className="flex flex-col space-y-4">
          {navItems.map((item) => (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={cn(
                "flex items-center space-x-4 px-4 py-3 rounded-xl transition-all duration-300 w-full text-left group",
                activeTab === item.id ? "bg-cyan-500/10 text-cyan-400 shadow-[inset_2px_0_0_#22d3ee]" : "text-gray-500 hover:text-gray-200 hover:bg-white/5"
              )}
            >
              <item.icon size={22} strokeWidth={activeTab === item.id ? 2.5 : 2} className="group-hover:scale-110 transition-transform" />
              <span className="font-bold tracking-wide">{item.label}</span>
            </button>
          ))}
        </nav>
      </div>

      {/* Mobile Bottom Nav */}
      <div className="md:hidden h-16 fixed bottom-0 w-full bg-[#0B0F14]/90 backdrop-blur-md border-t border-white/5 flex items-center justify-around z-50">
        {navItems.map((item) => (
          <button
            key={item.id}
            onClick={() => setActiveTab(item.id)}
            className={cn(
              "flex flex-col items-center justify-center w-full h-full space-y-1 transition-all duration-300",
              activeTab === item.id ? "text-cyan-400" : "text-gray-500 hover:text-gray-300"
            )}
          >
            <item.icon size={22} strokeWidth={activeTab === item.id ? 2.5 : 2} 
                       className={activeTab === item.id ? "drop-shadow-[0_0_8px_rgba(34,211,238,0.6)]" : ""} />
          </button>
        ))}
      </div>
    </>
  );
};

const Header = ({ title, rightIcon: RightIcon }) => (
  <header className="flex items-center justify-between px-6 pt-12 pb-4 bg-gradient-to-b from-[#0B0F14] to-transparent z-10 relative md:pt-8 md:px-8">
    <div className="flex items-center space-x-2 md:hidden">
      <div className="w-5 h-5 flex space-x-[2px] items-end">
        <div className="w-1 h-3 bg-cyan-400 rounded-sm shadow-[0_0_8px_rgba(34,211,238,0.6)] animate-pulse"></div>
        <div className="w-1 h-5 bg-cyan-400 rounded-sm shadow-[0_0_8px_rgba(34,211,238,0.6)] animate-pulse delay-75"></div>
        <div className="w-1 h-2 bg-cyan-400 rounded-sm shadow-[0_0_8px_rgba(34,211,238,0.6)] animate-pulse delay-150"></div>
      </div>
      <h1 className="text-xl font-black tracking-widest italic text-cyan-400 drop-shadow-[0_0_10px_rgba(34,211,238,0.3)] uppercase">EchoFlow</h1>
    </div>
    <div className="hidden md:block text-2xl font-bold text-white">{title || ''}</div>
    {RightIcon && <button className="text-gray-400 hover:text-white transition-colors ml-auto"><RightIcon size={24} /></button>}
  </header>
);

// --- SCREENS ---

// 1. ONBOARDING
const OnboardingFlow = ({ onComplete }) => {
  const [step, setStep] = useState(1);
  const [selectedTags, setSelectedTags] = useState([]);
  const tags = ['Comedy', 'Lo-Fi', 'Philosophy', 'Education', 'Business', 'Storytelling', 'Music', 'Productivity', 'Wellness', 'Tech'];

  const handleComplete = async () => {
    setStep(2);
    try {
      await new Promise(r => setTimeout(r, 2500));
      onComplete();
    } catch (e) {
      console.error(e);
      onComplete();
    }
  };

  if (step === 2) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#080b10] px-8 text-center relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-cyan-900/20 via-transparent to-purple-900/20" />
        
        <div className="w-16 h-16 bg-cyan-500/10 rounded-full flex items-center justify-center mb-8 border border-cyan-500/20 shadow-[0_0_30px_rgba(34,211,238,0.2)]">
           <div className="w-6 h-6 flex space-x-1 items-end justify-center">
             <div className="w-1 h-3 bg-cyan-400 rounded-sm animate-[bounce_1s_infinite]"></div>
             <div className="w-1 h-6 bg-cyan-400 rounded-sm animate-[bounce_1s_infinite_0.2s]"></div>
             <div className="w-1 h-4 bg-cyan-400 rounded-sm animate-[bounce_1s_infinite_0.4s]"></div>
           </div>
        </div>

        <h1 className="text-3xl font-bold text-white mb-4 tracking-tight drop-shadow-md">Building Your<br/>Feed</h1>
        <p className="text-gray-400 text-sm leading-relaxed max-w-[280px]">
          Curating high-fidelity audio streams based on your acoustic profile...
        </p>

        <div className="w-full mt-12 bg-white/5 border border-white/10 rounded-2xl p-4 blur-[1px]">
          <div className="flex items-center space-x-3 mb-6">
            <div className="w-10 h-10 rounded-full bg-white/10 animate-pulse" />
            <div className="space-y-2 flex-1">
              <div className="h-3 bg-white/10 rounded w-24 animate-pulse" />
              <div className="h-2 bg-white/10 rounded w-16 animate-pulse" />
            </div>
          </div>
          <div className="h-32 w-full flex items-end justify-center space-x-1 mb-6">
             {[...Array(12)].map((_, i) => (
                <div key={i} className="w-2 bg-white/10 rounded-t-sm" style={{ height: `${Math.random() * 100}%` }} />
             ))}
          </div>
          <div className="h-10 w-10 rounded-full bg-white/10 mx-auto animate-pulse" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full px-6 pt-16 pb-8 bg-[#0B0F14]">
      <h1 className="text-3xl font-bold text-white mb-2">Build Your Feed</h1>
      <p className="text-gray-400 mb-8">Select 3-5 genres to tune your algorithm.</p>
      
      <div className="flex flex-wrap gap-3 flex-1 content-start">
        {tags.map(tag => {
          const isSelected = selectedTags.includes(tag);
          return (
            <button
              key={tag}
              onClick={() => {
                if (isSelected) setSelectedTags(prev => prev.filter(t => t !== tag));
                else if (selectedTags.length < 5) setSelectedTags(prev => [...prev, tag]);
              }}
              className={cn(
                "px-5 py-3 rounded-full text-sm font-medium transition-all duration-300 transform",
                isSelected 
                  ? "bg-cyan-500/20 text-cyan-400 border border-cyan-400/50 shadow-[0_0_15px_rgba(34,211,238,0.3)] scale-105" 
                  : "bg-white/5 text-gray-400 border border-white/10 hover:bg-white/10 cursor-pointer"
              )}
            >
              {tag}
            </button>
          )
        })}
      </div>

      <div className="pt-4">
        <button 
          disabled={selectedTags.length < 3}
          onClick={handleComplete}
          className="w-full py-4 rounded-xl font-bold transition-all duration-300 disabled:opacity-50 disabled:bg-gray-800 disabled:text-gray-500 bg-cyan-400 text-black shadow-[0_0_20px_rgba(34,211,238,0.4)] cursor-pointer"
        >
          {selectedTags.length < 3 ? `Select ${3 - selectedTags.length} more` : 'Generate Feed'}
        </button>
      </div>
    </div>
  );
};

// 2. MAIN FEED
const FeedScreen = () => {
  const [clips, setClips] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setTimeout(() => {
      setClips([]); 
      setLoading(false);
    }, 1000);
  }, []);

  if (loading) return <div className="h-full flex items-center justify-center"><Loader2 className="animate-spin text-cyan-400" /></div>;

  if (clips.length === 0) {
    return (
      <div className="flex flex-col h-full bg-[#080B0E]">
        <Header rightIcon={Settings} />
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-center relative">
          <div className="absolute inset-0 bg-gradient-to-b from-transparent via-cyan-900/5 to-transparent pointer-events-none" />
          
          <div className="w-48 h-48 mb-8 relative opacity-40">
            <svg viewBox="0 0 100 100" className="w-full h-full drop-shadow-lg">
               <path d="M20,50 Q40,10 50,50 T80,50" fill="none" stroke="#22d3ee" strokeWidth="2" strokeLinecap="round" className="animate-[pulse_3s_infinite]" />
               <path d="M30,50 Q45,20 50,50 T70,50" fill="none" stroke="#a855f7" strokeWidth="1" strokeLinecap="round" className="animate-[pulse_3s_infinite_1s]" />
               <circle cx="50" cy="50" r="20" fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="1" />
            </svg>
          </div>

          <h2 className="text-xl font-semibold text-gray-200 mb-3">The air is quiet.</h2>
          <p className="text-gray-500 mb-8 max-w-[250px]">Start following creators to fill the silence.</p>
          
          <button className="flex items-center space-x-2 bg-cyan-400 hover:bg-cyan-300 text-black px-8 py-3.5 rounded-full font-bold uppercase tracking-wider text-sm transition-all shadow-[0_0_20px_rgba(34,211,238,0.3)] cursor-pointer">
            <span>Explore Channels</span>
            <ChevronLeft className="rotate-180" size={18} />
          </button>
        </div>
      </div>
    );
  }

  return null; 
};

// 3. EXPLORE SCREEN
const ExploreScreen = () => {
  return (
    <div className="h-full flex flex-col bg-[#080B0E] overflow-y-auto w-full">
      <Header title="Explore" rightIcon={Settings} />
      
      <div className="px-5 md:px-8 mt-2 max-w-5xl mx-auto w-full">
        <div className="relative mb-6">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-500" size={20} />
          <input 
            type="text" 
            placeholder="Search the library..." 
            className="w-full bg-white/5 border border-white/10 rounded-2xl py-3.5 pl-12 pr-12 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-cyan-400/50 transition-colors"
          />
          <button className="absolute right-3 top-1/2 -translate-y-1/2 p-1.5 bg-white/5 rounded-lg text-gray-400 cursor-pointer hover:text-white transition-colors">
            <SlidersHorizontal size={16} />
          </button>
        </div>

        <div className="flex space-x-3 overflow-x-auto pb-4 scrollbar-hide">
          <button className="px-5 py-2 rounded-full border border-cyan-400 text-cyan-400 text-xs font-bold tracking-wider uppercase whitespace-nowrap shadow-[0_0_10px_rgba(34,211,238,0.2)] cursor-pointer">All</button>
          <button className="px-5 py-2 rounded-full bg-white/5 text-gray-400 text-xs font-bold tracking-wider uppercase whitespace-nowrap cursor-pointer hover:bg-white/10">Trending</button>
          <button className="px-5 py-2 rounded-full bg-white/5 text-gray-400 text-xs font-bold tracking-wider uppercase whitespace-nowrap cursor-pointer hover:bg-white/10">Music</button>
          <button className="px-5 py-2 rounded-full bg-white/5 text-gray-400 text-xs font-bold tracking-wider uppercase whitespace-nowrap cursor-pointer hover:bg-white/10">Education</button>
        </div>
      </div>

      <div className="px-5 md:px-8 mt-4 max-w-5xl mx-auto w-full">
        <div className="flex items-center space-x-2 mb-4">
          <h2 className="text-xl font-bold text-white">Trending Now</h2>
          <span className="text-purple-500">🔥</span>
        </div>

        <div className="relative h-48 md:h-64 w-full rounded-2xl overflow-hidden mb-8 group cursor-pointer border border-white/5 hover:border-white/20 transition-colors">
          <div className="absolute inset-0 bg-gradient-to-tr from-[#0f172a] to-[#1e1b4b] mix-blend-overlay" />
          <div className="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiPjxwb2x5bGluZSBwb2ludHM9IjAsMTAwIDUwLDYwIDEwMCw4MCAxNTAsNDAgMjAwLDcwIDI1MCwzMCAzMDAsOTAiIGZpbGw9Im5vbmUiIHN0cm9rZT0icmdiYSgyNTUsMjU1LDI1NSwwLjEpIiBzdHJva2Utd2lkdGg9IjIiLz48L3N2Zz4=')] bg-cover opacity-50" />
          <div className="absolute inset-0 bg-gradient-to-t from-[#0B0F14] via-transparent to-transparent" />
          
          <div className="absolute bottom-4 md:bottom-8 left-4 md:left-8 right-4 md:right-8 flex items-end justify-between">
            <div>
              <p className="text-[10px] md:text-xs font-bold tracking-widest text-pink-400 uppercase mb-1">Featured Mix</p>
              <h3 className="text-lg md:text-3xl font-bold text-white leading-tight mb-1">Neon Genesis Audio</h3>
              <p className="text-sm md:text-base text-gray-400">By Synapse</p>
            </div>
            <button className="w-12 h-12 md:w-16 md:h-16 bg-cyan-400 rounded-full flex items-center justify-center text-black shadow-[0_0_20px_rgba(34,211,238,0.5)] group-hover:scale-110 transition-transform">
              <Play size={24} className="ml-1" fill="currentColor" />
            </button>
          </div>
        </div>

        <div className="flex justify-between items-end mb-4 mt-8">
          <h2 className="text-lg font-bold text-white">Suggested Creators</h2>
          <button className="text-xs font-medium text-gray-400 hover:text-white transition-colors cursor-pointer">View All</button>
        </div>
        <div className="flex space-x-6 overflow-x-auto pb-4 scrollbar-hide">
           {['Aria V.', 'Kaelen', 'Nova Beat', 'Synthesis', 'Dr. Echo'].map((name, i) => (
             <div key={i} className="flex flex-col items-center space-y-2 cursor-pointer group">
               <div className={cn("w-16 h-16 md:w-20 md:h-20 rounded-full bg-gray-800 border-2 relative group-hover:scale-105 transition-transform", i === 0 ? "border-cyan-400 shadow-[0_0_15px_rgba(34,211,238,0.3)]" : "border-transparent group-hover:border-white/20")}>
                  {i===0 && <div className="absolute bottom-0 right-0 w-3 h-3 md:w-4 md:h-4 bg-cyan-400 rounded-full border-2 border-[#0B0F14]"></div>}
               </div>
               <span className="text-xs md:text-sm font-medium text-gray-300 group-hover:text-white transition-colors">{name}</span>
             </div>
           ))}
        </div>

        <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pb-24">
          {DUMMY_EXPLORE_CLIPS.map((clip) => (
            <div key={clip.id} className={cn("flex items-center justify-between p-4 rounded-2xl border transition-all cursor-pointer", clip.active ? "bg-white/5 border-cyan-400/30 shadow-[inset_2px_0_0_#22d3ee]" : "bg-[#0f131a] border-white/5 hover:bg-white/10 hover:border-white/10")}>
              <div className="flex items-center space-x-4">
                <div className="w-12 h-12 rounded-xl bg-black/50 border border-white/5 flex items-center justify-center overflow-hidden relative">
                   <div className={cn("w-full h-1/2 absolute bottom-0 opacity-30 blur-sm", clip.color.replace('text-', 'bg-'))}></div>
                   <div className="flex space-x-0.5 h-4 items-end z-10">
                      {[...Array(5)].map((_, i) => (
                        <div key={i} className={cn("w-0.5 rounded-sm", clip.color, clip.active ? "animate-pulse" : "")} style={{height: `${Math.random() * 100}%`}}></div>
                      ))}
                   </div>
                </div>
                <div>
                  <h4 className={cn("text-sm font-semibold mb-0.5", clip.active ? "text-cyan-400" : "text-gray-200")}>{clip.title}</h4>
                  <p className="text-xs text-gray-500 font-medium">{clip.creator}</p>
                </div>
              </div>
              <div className="flex items-center space-x-3">
                <span className="text-xs text-gray-600 font-mono hidden md:inline-block">{clip.duration}</span>
                {clip.active ? (
                  <button className="w-8 h-8 rounded-full bg-cyan-900/40 text-cyan-400 flex items-center justify-center border border-cyan-400/30">
                    <Pause size={14} fill="currentColor" />
                  </button>
                ) : (
                  <button className="w-8 h-8 rounded-full bg-white/5 text-gray-400 flex items-center justify-center hover:bg-white/20 hover:text-white transition-colors">
                    <Play size={14} className="ml-0.5" fill="currentColor" />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

// 4. UPLOAD/CREATE SCREEN
const UploadScreen = ({ onCancel }) => {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('Music');
  const [theme, setTheme] = useState('Neon Cyan');

  const handlePost = () => {
    console.log("POST /clips/", { title, category, theme });
    onCancel();
  };

  return (
    <div className="h-full flex flex-col bg-[#0B0F14] overflow-y-auto w-full">
      <header className="flex items-center justify-between px-5 py-4 border-b border-white/5 md:px-8">
        <button onClick={onCancel} className="text-gray-400 hover:text-white text-sm transition-colors cursor-pointer">Cancel</button>
        <h1 className="text-lg font-bold text-white">New Clip</h1>
        <button onClick={handlePost} className="text-cyan-400 font-medium hover:text-cyan-300 text-sm transition-colors cursor-pointer">Post</button>
      </header>

      <div className="p-5 flex-1 pb-24 max-w-2xl mx-auto w-full md:mt-8">
        <div className="bg-[#121820] rounded-2xl p-6 mb-8 border border-white/5 relative overflow-hidden group">
          <div className="absolute inset-0 bg-gradient-to-b from-cyan-900/10 to-transparent pointer-events-none" />
          
          <div className="flex flex-col items-center justify-center space-y-8 relative z-10">
            <button className="w-16 h-16 bg-cyan-400 rounded-full flex items-center justify-center shadow-[0_0_30px_rgba(34,211,238,0.5)] transition-transform hover:scale-105 active:scale-95 cursor-pointer">
              <Play size={28} className="ml-1 text-[#0B0F14]" fill="currentColor" />
            </button>
            
            <div className="w-full flex items-center justify-between space-x-4">
              <span className="text-xs text-white font-mono">0:00</span>
              <div className="flex-1 h-1 bg-white/10 rounded-full relative">
                <div className="absolute top-0 left-0 h-full w-1/3 bg-cyan-400 rounded-full shadow-[0_0_10px_rgba(34,211,238,0.6)]"></div>
                <div className="absolute top-1/2 left-1/3 -translate-y-1/2 -translate-x-1/2 w-3 h-6 bg-cyan-400 rounded shadow-[0_0_10px_rgba(34,211,238,0.8)]"></div>
              </div>
              <span className="text-xs text-gray-500 font-mono">0:45</span>
            </div>
          </div>
        </div>

        <div className="space-y-6">
          <div>
            <input 
              type="text" 
              placeholder="Title your sonic journey..." 
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full bg-transparent text-xl font-bold text-white placeholder-gray-500 focus:outline-none border-b border-transparent focus:border-cyan-400/50 pb-2 transition-colors"
            />
          </div>
          <div>
            <textarea 
              placeholder="Describe the vibe, the context, or the story behind this clip..." 
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full bg-transparent text-sm text-gray-300 placeholder-gray-600 focus:outline-none resize-none h-20"
            />
          </div>

          <div className="flex space-x-3 overflow-x-auto pb-2 scrollbar-hide">
            {['Music', 'Podcast', 'Lo-Fi', 'Education', 'Storytelling'].map(cat => (
              <button 
                key={cat}
                onClick={() => setCategory(cat)}
                className={cn(
                  "px-5 py-2.5 rounded-full text-sm font-medium whitespace-nowrap transition-all cursor-pointer",
                  category === cat 
                    ? "border border-cyan-400 text-cyan-400 bg-cyan-400/10" 
                    : "bg-white/5 text-gray-400 hover:bg-white/10"
                )}
              >
                {cat}
              </button>
            ))}
          </div>

          <div className="pt-4">
            <h3 className="text-xs font-bold text-gray-300 tracking-wider uppercase mb-4">Visualizer Theme</h3>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {[
                { name: 'Neon Cyan', cls: 'border-cyan-400 bg-cyan-900/20' },
                { name: 'Deep Violet', cls: 'border-transparent bg-purple-900/20' },
                { name: 'Crimson Pulse', cls: 'border-transparent bg-red-900/20' }
              ].map(t => (
                <button 
                  key={t.name}
                  onClick={() => setTheme(t.name)}
                  className={cn(
                    "h-24 rounded-xl flex items-end p-3 text-left border relative overflow-hidden transition-all cursor-pointer hover:opacity-80",
                    theme === t.name ? t.cls : "border-white/5 bg-[#121820]"
                  )}
                >
                  <div className={cn("absolute inset-0 opacity-40 blur-xl", t.cls.split(' ')[1])} />
                  <span className={cn("text-xs font-bold z-10 relative", theme === t.name ? "text-white" : "text-gray-500")}>
                    {t.name.split(' ').map(w=><React.Fragment key={w}>{w}<br/></React.Fragment>)}
                  </span>
                </button>
              ))}
            </div>
          </div>
          
          <button className="flex items-center space-x-2 text-gray-400 text-sm mt-6 cursor-pointer hover:text-white transition-colors">
             <span>Advanced Options</span>
             <ChevronLeft className="-rotate-90" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
};

// 5. SAVED / LIBRARY SCREEN
const SavedScreen = () => {
  const [activeSubTab, setActiveSubTab] = useState('Recently Saved');

  return (
    <div className="h-full flex flex-col bg-[#0B0F14] overflow-y-auto w-full">
      <Header title="Library" rightIcon={Settings} />
      
      <div className="px-5 md:px-8 mt-2 max-w-5xl mx-auto w-full">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-3xl font-bold text-white md:hidden">Saved</h1>
          <div className="flex bg-white/5 rounded-lg p-1 ml-auto">
            <button className="p-1.5 bg-cyan-900/40 text-cyan-400 rounded border border-cyan-400/30 cursor-pointer"><Grid size={18} /></button>
            <button className="p-1.5 text-gray-500 hover:text-gray-300 transition-colors cursor-pointer"><ListIcon size={18} /></button>
          </div>
        </div>

        <div className="flex space-x-3 overflow-x-auto pb-6 scrollbar-hide">
          {['Recently Saved', 'Most Played', 'Downloaded'].map(tab => (
            <button 
              key={tab}
              onClick={() => setActiveSubTab(tab)}
              className={cn(
                "px-4 py-2 rounded-full text-xs font-bold tracking-wider whitespace-nowrap transition-all cursor-pointer",
                activeSubTab === tab 
                  ? "border border-cyan-400 text-cyan-400 bg-cyan-400/10 shadow-[0_0_10px_rgba(34,211,238,0.15)]" 
                  : "bg-white/5 text-gray-400 hover:bg-white/10"
              )}
            >
              {tab}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 md:gap-6 pb-24">
          {DUMMY_SAVED_CLIPS.map(clip => (
            <div key={clip.id} className="bg-[#121820] border border-white/5 rounded-2xl p-4 flex flex-col cursor-pointer hover:bg-white/5 group transition-all">
              <div className="aspect-square bg-black/50 rounded-xl mb-3 flex items-center justify-center relative overflow-hidden border border-white/5 group-hover:border-white/10 transition-colors">
                <div className={cn("absolute inset-0 opacity-40 blur-xl", clip.image)} />
                <div className="flex items-center space-x-0.5 h-8 z-10 relative">
                  {[...Array(5)].map((_, i) => (
                    <div key={i} className={cn("w-1 rounded-sm opacity-80", clip.color)} style={{height: `${40 + Math.random() * 60}%`}}></div>
                  ))}
                </div>
                {clip.id === 1 && <div className="absolute w-3/4 h-3/4 border border-cyan-400/20 rounded-full z-0"></div>}
              </div>
              <h3 className="text-sm font-semibold text-gray-100 truncate group-hover:text-cyan-400 transition-colors">{clip.title}</h3>
              <p className="text-xs text-gray-500 mt-1">{clip.creator}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

// 6. PROFILE SCREEN
const ProfileScreen = () => {
  const [activeTab, setActiveTab] = useState('Clips');

  return (
    <div className="h-full flex flex-col bg-[#0B0F14] overflow-y-auto w-full">
      <Header title="Profile" rightIcon={Settings} />
      
      <div className="px-5 md:px-8 mt-4 flex flex-col items-center pb-24 max-w-3xl mx-auto w-full">
        <div className="relative mb-6">
          <div className="w-28 h-28 md:w-36 md:h-36 rounded-full p-1 bg-gradient-to-tr from-cyan-400 via-blue-500 to-purple-500 shadow-[0_0_30px_rgba(34,211,238,0.3)]">
            <div className="w-full h-full rounded-full bg-gray-800 overflow-hidden border-2 border-black">
              <img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiPjxyZWN0IHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiIGZpbGw9IiMxZjI5MzciLz48Y2lyY2xlIGN4PSI1MCIgY3k9IjQwIiByPSIxNSIgZmlsbD0iIzRmNDZlNSIvPjxwYXRoIGQ9Ik0gMjAgMTAwIEMgMjAgNjAgODAgNjAgODAgMTAwIiBmaWxsPSIjNGY0NmU1Ii8+PC9zdmc+" alt="Avatar" className="w-full h-full object-cover" />
            </div>
          </div>
          <button className="absolute bottom-2 right-0 w-8 h-8 md:w-10 md:h-10 bg-[#1f2937] border border-white/10 rounded-full flex items-center justify-center text-white shadow-lg hover:bg-gray-700 transition-colors cursor-pointer">
            <Share2 size={16} />
          </button>
        </div>

        <h1 className="text-2xl md:text-3xl font-bold text-white tracking-tight">Kaelen Vance</h1>
        <p className="text-sm md:text-base text-gray-400 mb-4">@kaelenv_audio</p>
        
        <p className="text-center text-sm md:text-base text-gray-300 leading-relaxed mb-6 px-4 max-w-xl">
          Sound designer and modular synthesis enthusiast. Crafting deep, hypnotic soundscapes that react to the pulse of the digital world.
        </p>

        <button 
           className="px-10 py-3 md:px-12 md:py-3.5 bg-gradient-to-r from-cyan-400 to-cyan-200 text-black font-bold rounded-full text-sm tracking-wider uppercase shadow-[0_0_20px_rgba(34,211,238,0.5)] hover:scale-105 transition-transform cursor-pointer"
        >
          Edit Profile
        </button>

        <div className="w-full flex justify-between px-6 py-8 border-b border-white/5 mb-2 mt-4 max-w-lg">
          <div className="text-center">
            <p className="text-xl md:text-2xl font-bold text-white">14.2K</p>
            <p className="text-[10px] md:text-xs text-gray-500 uppercase tracking-widest font-bold mt-1">Followers</p>
          </div>
          <div className="text-center">
            <p className="text-xl md:text-2xl font-bold text-white">284</p>
            <p className="text-[10px] md:text-xs text-gray-500 uppercase tracking-widest font-bold mt-1">Following</p>
          </div>
          <div className="text-center">
            <p className="text-xl md:text-2xl font-bold text-white">1.2M</p>
            <p className="text-[10px] md:text-xs text-gray-500 uppercase tracking-widest font-bold mt-1">Total Likes</p>
          </div>
        </div>

        <div className="w-full flex mt-2 max-w-2xl">
          {['Clips', 'Saved', 'Liked'].map(tab => (
            <button 
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={cn(
                "flex-1 py-4 text-sm font-bold tracking-wider relative transition-colors cursor-pointer",
                activeTab === tab ? "text-cyan-400" : "text-gray-500 hover:text-gray-300"
              )}
            >
              {tab}
              {activeTab === tab && (
                <div className="absolute bottom-0 left-1/4 right-1/4 h-0.5 bg-cyan-400 shadow-[0_0_8px_rgba(34,211,238,0.8)]" />
              )}
            </button>
          ))}
        </div>
        
        <div className="w-full mt-4 bg-white/5 rounded-2xl p-4 border border-white/5 flex items-center justify-between cursor-pointer hover:bg-white/10 transition-colors max-w-2xl">
           <button className="w-10 h-10 bg-cyan-900/40 rounded-lg flex items-center justify-center text-cyan-400 border border-cyan-400/20">
             <Play size={16} fill="currentColor" />
           </button>
           <span className="text-xs font-bold text-gray-500 uppercase tracking-widest">New Release</span>
        </div>

      </div>
    </div>
  );
};