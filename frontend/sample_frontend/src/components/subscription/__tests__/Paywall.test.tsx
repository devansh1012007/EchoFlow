import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { Paywall } from '../Paywall';

const mocks = vi.hoisted(() => ({
  getManageUrl: vi.fn(),
  sync: vi.fn(),
  getStatus: vi.fn(),
}));

vi.mock('../../../api/client', () => ({
  subscriptionAPI: {
    getStatus: mocks.getStatus,
    sync: mocks.sync,
    getManageUrl: mocks.getManageUrl,
  },
}));

vi.mock('../../../stores/auth', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'testuser' },
    authed: true,
    loading: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    patchUser: vi.fn(),
  }),
}));

describe('Paywall', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getManageUrl.mockResolvedValue({ url: 'https://rcat.page/p/test' });
    mocks.sync.mockResolvedValue({ detail: 'Synced' });
  });

  it('renders upgrade button', () => {
    render(<Paywall />);
    expect(screen.getByText('Upgrade to Pro')).toBeInTheDocument();
  });

  it('calls getManageUrl on mount', async () => {
    render(<Paywall />);
    await waitFor(() => {
      expect(mocks.getManageUrl).toHaveBeenCalledTimes(1);
    });
  });

  it('calls handleUpgrade and triggers sync + getManageUrl', async () => {
    render(<Paywall />);
    const btn = screen.getByText('Upgrade to Pro');
    btn.click();
    await waitFor(() => {
      expect(mocks.sync).toHaveBeenCalled();
    });
  });
});
