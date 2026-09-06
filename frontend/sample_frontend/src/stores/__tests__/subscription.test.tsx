import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { SubscriptionProvider, useSubscription } from '../subscription';

const mocks = vi.hoisted(() => ({
  getStatus: vi.fn(),
  getManageUrl: vi.fn(),
}));

vi.mock('../../api/client', () => ({
  subscriptionAPI: {
    getStatus: mocks.getStatus,
    getManageUrl: mocks.getManageUrl,
  },
}));

vi.mock('@revenuecat/purchases-js', () => ({
  setup: vi.fn(),
  default: { setup: vi.fn() },
}));

describe('useSubscription', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('initializes with null status and loading=true', () => {
    const { result } = renderHook(() => useSubscription(), {
      wrapper: ({ children }) => <SubscriptionProvider>{children}</SubscriptionProvider>,
    });
    expect(result.current.status).toBeNull();
    expect(result.current.loading).toBe(true);
    expect(result.current.isPro).toBe(false);
  });

  it('sets isPro when status has is_pro=true', async () => {
    mocks.getStatus.mockResolvedValue({
      is_pro: true,
      expires_at: '2026-12-31T23:59:59Z',
      grace_until: null,
      last_synced: '2026-09-06T00:00:00Z',
      limits: { daily_uploads_remaining: 'unlimited' },
    });

    const { result } = renderHook(() => useSubscription(), {
      wrapper: ({ children }) => <SubscriptionProvider>{children}</SubscriptionProvider>,
    });

    await waitFor(() => {
      expect(result.current.isPro).toBe(true);
      expect(result.current.loading).toBe(false);
    });
  });

  it('sets isPro=false for free user', async () => {
    mocks.getStatus.mockResolvedValue({
      is_pro: false,
      expires_at: null,
      grace_until: null,
      last_synced: '2026-09-06T00:00:00Z',
      limits: { daily_uploads_remaining: '5' },
    });

    const { result } = renderHook(() => useSubscription(), {
      wrapper: ({ children }) => <SubscriptionProvider>{children}</SubscriptionProvider>,
    });

    await waitFor(() => {
      expect(result.current.isPro).toBe(false);
      expect(result.current.loading).toBe(false);
    });
  });
});
