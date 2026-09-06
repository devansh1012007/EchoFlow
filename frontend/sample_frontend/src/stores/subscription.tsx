import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { subscriptionAPI, SubscriptionStatus } from '../api/client';

interface SubscriptionContextValue {
  status: SubscriptionStatus | null;
  loading: boolean;
  isPro: boolean;
  refresh: () => Promise<void>;
  openManagement: () => void;
}

const SubscriptionContext = createContext<SubscriptionContextValue | null>(null);
export const useSubscription = () => useContext(SubscriptionContext)!;

export function SubscriptionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SubscriptionStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await subscriptionAPI.getStatus();
      setStatus(data);
      // Identify the user with RevenueCat SDK using their app user ID.
      try {
        const Purchases = await import('@revenuecat/purchases-js');
        const publicKey = import.meta.env.VITE_REVENUECAT_PUBLIC_KEY;
        if (publicKey) {
          Purchases.setup(publicKey);
        }
      } catch {
        // RevenueCat SDK not configured — skip silently
      }
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const openManagement = useCallback(() => {
    subscriptionAPI.getManageUrl().then(d => {
      window.open(d.url, '_blank');
    });
  }, []);

  useEffect(() => {
    refresh();
    // Refresh subscription status every 5 minutes
    const id = setInterval(refresh, 300000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <SubscriptionContext.Provider value={{
      status,
      loading,
      isPro: status?.is_pro ?? false,
      refresh,
      openManagement,
    }}>
      {children}
    </SubscriptionContext.Provider>
  );
}
