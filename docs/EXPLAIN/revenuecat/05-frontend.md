# RevenueCat Integration — Frontend

## SDK Integration

### Package

```json
{
  "dependencies": {
    "@revenuecat/purchases-js": "^1.58.0"
  }
}
```

### Initialization (`main.tsx`)

```typescript
import { Purchases } from '@revenuecat/purchases-js';
import { useAuth } from './stores/auth';

// Initialize after auth context is available
const publicKey = import.meta.env.VITE_REVENUECAT_PUBLIC_KEY;
const appUserId = user.uuid; // From auth context

Purchases.setup(publicKey, appUserId);
```

**Key points:**
- Public key from `VITE_REVENUECAT_PUBLIC_KEY` (safe to expose)
- App User ID = Django `User.uuid` (string)
- SDK initialized once per session after login

### SDK Usage in Components

```typescript
// Fetch offerings (Pro package)
const offerings = await Purchases.getOfferings();
const proPackage = offerings.current?.availablePackages.find(
  p => p.product.identifier === 'pro_annual' || p.product.identifier === 'pro_monthly'
);

// Purchase
await Purchases.purchasePackage(proPackage);

// Open Customer Portal (manage subscription)
await Purchases.openCustomerCenter();
```

## Subscription Context (`stores/subscription.tsx`)

### Provider

```typescript
export function SubscriptionProvider({ children }) {
  const [status, setStatus] = useState<SubscriptionStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    const data = await subscriptionAPI.getStatus();
    setStatus(data);
  };

  const openManagement = () => {
    subscriptionAPI.getManageUrl().then(d => window.open(d.url, '_blank'));
  };

  // Auto-refresh every 5 minutes
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 300000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <SubscriptionContext.Provider value={{ status, loading, isPro: status?.is_pro, refresh, openManagement }}>
      {children}
    </SubscriptionContext.Provider>
  );
}
```

### Hook

```typescript
export const useSubscription = () => useContext(SubscriptionContext)!;

// Usage in components
const { isPro, loading, refresh, openManagement } = useSubscription();
```

## Paywall Component (`Paywall.tsx`)

### Features

- Modal overlay with Pro upgrade CTA
- "Upgrade to Pro" button → triggers sync → opens Customer Portal
- "Maybe later" dismiss option
- Loading state during sync

### Usage

```typescript
// In AppShell.tsx (always available for free users)
{showPaywall && <Paywall onDone={() => setShowPaywall(false)} />}

// Or in any gated feature
{!isPro && <Paywall onDone={() => navigate('/settings')} />}
```

### Paywall Flow

```
User clicks "Upgrade to Pro"
        ↓
Paywall: loading = true
        ↓
subscriptionAPI.sync() → POST /subscription/sync/
        ↓ (sync completes)
subscriptionAPI.getManageUrl() → GET /subscription/manage/
        ↓
window.location.href = manageUrl
        ↓
User manages subscription in RevenueCat portal
        ↓
User returns → frontend auto-refreshes status (5min interval or manual refresh)
```

## API Client (`subscriptionAPI`)

```typescript
export interface SubscriptionStatus {
  is_pro: boolean;
  expires_at: string | null;
  grace_until: string | null;
  last_synced: string;
  limits: Record<string, string>;
}

export const subscriptionAPI = {
  getStatus: () => api('/subscription/'),
  sync: () => api('/subscription/sync/', { method: 'POST' }),
  getManageUrl: () => api('/subscription/manage/'),
};
```

## Auth Integration (`stores/auth.tsx`)

After login/register, configure RevenueCat SDK:

```typescript
const login = async (username: string, password: string) => {
  const d = await authAPI.login(username, password);
  persist(d, d.user);
  
  // Configure RevenueCat SDK with user's app_user_id
  const Purchases = await import('@revenuecat/purchases-js');
  const publicKey = import.meta.env.VITE_REVENUECAT_PUBLIC_KEY;
  if (publicKey) {
    Purchases.setup(publicKey, d.user.uuid);
  }
};
```

## Environment Variables

```bash
# Frontend (.env.local)
VITE_REVENUECAT_PUBLIC_KEY=pk_live_xxx

# Backend (.env)
REVENUECAT_PUBLIC_KEY=pk_live_xxx
```

## Testing

```bash
# Run frontend tests
cd frontend/sample_frontend
npx vitest run src/stores/__tests__/subscription.test.tsx
npx vitest run src/components/subscription/__tests__/Paywall.test.tsx
```

### Test Patterns

```typescript
// Mocking subscriptionAPI with vi.hoisted
const mocks = vi.hoisted(() => ({
  getManageUrl: vi.fn(),
  sync: vi.fn(),
  getStatus: vi.fn(),
}));

vi.mock('../../api/client', () => ({
  subscriptionAPI: {
    getStatus: mocks.getStatus,
    sync: mocks.sync,
    getManageUrl: mocks.getManageUrl,
  },
}));

// Mocking RevenueCat SDK
vi.mock('@revenuecat/purchases-js', () => ({
  setup: vi.fn(),
  default: { setup: vi.fn() },
}));

// Testing useSubscription
const { result } = renderHook(() => useSubscription(), {
  wrapper: ({ children }) => <SubscriptionProvider>{children}</SubscriptionProvider>,
});
```

## Paywall Integration Points

| Location | Trigger |
|---|---|
| `AppShell.tsx` | Bottom "Upgrade to Pro" button for free users |
| Upload page | When free user hits daily limit |
| Feed | When free user tries to play HD clip |
| Settings | "Manage Subscription" button |

## Common Patterns

### Checking Pro Status in Components

```typescript
const { isPro, loading } = useSubscription();

if (loading) return <Spinner />;
if (!isPro) return <Paywall />;

return <ProFeature />;
```

### Gating Features

```typescript
// In any component
const { isPro } = useSubscription();

const handleAction = () => {
  if (!isPro) {
    setShowPaywall(true);
    return;
  }
  // Pro-only action
};
```

### Refreshing Status

```typescript
const { refresh } = useSubscription();

// After purchase or manual sync
await refresh();
```